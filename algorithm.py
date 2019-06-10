import numpy as np
import pandas as pd
from quantopian.pipeline import Pipeline
import quantopian.algorithm as algo
from quantopian.pipeline.data.builtin import USEquityPricing
from quantopian.pipeline.factors import SimpleMovingAverage
from quantopian.pipeline.factors import AnnualizedVolatility
from quantopian.pipeline.filters import QTradableStocksUS

#ExposureMngr class from https://www.quantopian.com/posts/leverage-and-long-slash-short-exposure-one-class-to-rule-them-all
class ExposureMngr(object):
    def __init__(self, target_leverage = 1.0, target_long_exposure_perc = 0.50, target_short_exposure_perc = 0.50):   
        self.target_leverage            = target_leverage
        self.target_long_exposure_perc  = target_long_exposure_perc              
        self.target_short_exposure_perc = target_short_exposure_perc           
        self.short_exposure             = 0.0
        self.long_exposure              = 0.0
        self.open_order_short_exposure  = 0.0
        self.open_order_long_exposure   = 0.0
      
    def get_current_leverage(self, context, consider_open_orders = True):
        curr_cash = context.portfolio.cash - (self.short_exposure * 2)
        if consider_open_orders:
            curr_cash -= self.open_order_short_exposure
            curr_cash -= self.open_order_long_exposure
        curr_leverage = (context.portfolio.portfolio_value - curr_cash) / context.portfolio.portfolio_value
        return curr_leverage

    def get_exposure(self, context, consider_open_orders = True):
        long_exposure, short_exposure = self.get_long_short_exposure(context, consider_open_orders)
        return long_exposure + short_exposure
    
    def get_long_short_exposure(self, context, consider_open_orders = True):
        long_exposure         = self.long_exposure
        short_exposure        = self.short_exposure
        if consider_open_orders:
            long_exposure  += self.open_order_long_exposure
            short_exposure += self.open_order_short_exposure     
        return (long_exposure, short_exposure)
    
    def get_long_short_exposure_pct(self, context, consider_open_orders = True, consider_unused_cash = True):
        long_exposure, short_exposure = self.get_long_short_exposure(context, consider_open_orders)        
        total_cash = long_exposure + short_exposure
        if consider_unused_cash:
            total_cash += self.get_available_cash(context, consider_open_orders)
        long_exposure_pct   = long_exposure  / total_cash if total_cash > 0 else 0
        short_exposure_pct  = short_exposure / total_cash if total_cash > 0 else 0
        return (long_exposure_pct, short_exposure_pct)
    
    def get_available_cash(self, context, consider_open_orders = True):
        curr_cash = context.portfolio.cash - (self.short_exposure * 2)
        if consider_open_orders:
            curr_cash -= self.open_order_short_exposure
            curr_cash -= self.open_order_long_exposure            
        leverage_cash = context.portfolio.portfolio_value * (self.target_leverage - 1.0)
        return curr_cash + leverage_cash
          
    def get_available_cash_long_short(self, context, consider_open_orders = True):
        total_available_cash  = self.get_available_cash(context, consider_open_orders)
        long_exposure         = self.long_exposure
        short_exposure        = self.short_exposure
        if consider_open_orders:
            long_exposure  += self.open_order_long_exposure
            short_exposure += self.open_order_short_exposure
        current_exposure       = long_exposure + short_exposure + total_available_cash
        target_long_exposure  = current_exposure * self.target_long_exposure_perc
        target_short_exposure = current_exposure * self.target_short_exposure_perc        
        long_available_cash   = target_long_exposure  - long_exposure 
        short_available_cash  = target_short_exposure - short_exposure
        return (long_available_cash, short_available_cash)
    
    def update(self, context, data):
        #
        # calculate cash needed to complete open orders
        #
        self.open_order_short_exposure  = 0.0
        self.open_order_long_exposure   = 0.0
        for stock, orders in  get_open_orders().iteritems():
            price = data.current(stock, 'price')
            if np.isnan(price):
                continue
            amount = 0 if stock not in context.portfolio.positions else context.portfolio.positions[stock].amount
            for oo in orders:
                order_amount = oo.amount - oo.filled
                if order_amount < 0 and amount <= 0:
                    self.open_order_short_exposure += (price * -order_amount)
                elif order_amount > 0 and amount >= 0:
                    self.open_order_long_exposure  += (price * order_amount)
            
        #
        # calculate long/short positions exposure
        #
        self.short_exposure = 0.0
        self.long_exposure  = 0.0
        for stock, position in context.portfolio.positions.iteritems():  
            amount = position.amount  
            last_sale_price = position.last_sale_price  
            if amount < 0:
                self.short_exposure += (last_sale_price * -amount)
            elif amount > 0:
                self.long_exposure  += (last_sale_price * amount)

# initialize
def initialize(context):
    algo.attach_pipeline(make_pipeline(context), 'my_pipeline')
    
    context.stocks_stored = {}
    
    schedule_function(
        purchase_daily,
        date_rules.every_day(),
        time_rules.market_open(hours=0,minutes=30)
    )
                      
#    set_long_only()
                      
#    set_max_leverage(1)

    context.exposure = ExposureMngr(target_leverage = 1.0,  
                                    target_long_exposure_perc = 1.0,  
                                    target_short_exposure_perc = 0.0)

    # make pipeline
def make_pipeline(context):
    base_universe = QTradableStocksUS()
    #Create Price Filter
    latest_close = USEquityPricing.close.latest
    penny_stock = latest_close < 5
    # Create Momentum Filter
    SMA200 = SimpleMovingAverage(inputs=[USEquityPricing.close],window_length=200)
    SMA20 = SimpleMovingAverage(inputs=[USEquityPricing.close],window_length=20)
    momentum_filter = SMA20 > SMA200
    momentum_and_price = momentum_filter & penny_stock    
    #Create Volatility Filter
    volatility = AnnualizedVolatility(
        inputs=[USEquityPricing.close],
        window_length=30,
        mask=base_universe
    )
    high_volatility = volatility.top(1,mask=momentum_and_price)

    # Combine into final filter
    final_filter = penny_stock & momentum_filter & high_volatility
    
    return Pipeline(
        columns={
            'SMA200': SMA200,
            'SMA20' : SMA20,
            'Last Close': latest_close,
            'Annualized Volatility':volatility,
            'final filter':final_filter
        },
        screen=final_filter
    )


# Record variables: pipeline output, cost basis, upper and lower limit sell points, and maybe more
def before_trading_start(context,data):
    # pipeline_output returns a pandas DataFrame with the results of our factors and filters.
    # Since the pipeline only returns a single stock, this should also be the stock to purchase
    context.output = algo.pipeline_output('my_pipeline')
    # define security to buy (this returns an equity object)
    context.purchase = context.output['final filter'].index
    print(context.purchase)
    # define buy & sell limits from the cost basis (moved to handle_data)

# Track current price to know when it crosses the sell boundaries    
def handle_data(context,data):
    context.exposure.update(context, data)   
    pfvalue = context.exposure.get_exposure(context, consider_open_orders = False)
    
    for stock in context.portfolio.positions:    
        context.current_price = data.current(stock,'price')

    for stock in context.portfolio.positions:
        basis = context.portfolio.positions[stock].cost_basis
        context.sell_high = 1.1 * basis
        context.sell_low = 0.95 * basis
        
    open_orders = get_open_orders()
    # some kind of loop to check if I own something
    # if yes, measure price
        # if it hits one of the bands, sell at a limit order
    # if no, purchase the new stock from my pipeline
        # Might want to do this as a limit buy from the current price
#    def rebalance(context,data):
   # print(context.portfolio.positions_value)
    for security in context.portfolio.positions:
        if len(open_orders) > 0:
            print('Already an open order')
            break
        elif context.current_price <= context.sell_low and data.can_trade(security):
            order_target_percent(security, 0)
        elif context.current_price >= context.sell_high and data.can_trade(security):
            order_target_percent(security, 0)
        else:
            pass
        
        
def purchase_daily(context,data):
    cash = context.exposure.get_available_cash(context, consider_open_orders = False)  

    for stock in context.purchase:
        if len(context.portfolio.positions) > 0:
                print('Portfolio filled')      
                break
        elif data.can_trade(stock):
            order_value(stock,cash)
