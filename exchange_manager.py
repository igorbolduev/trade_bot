from pybit.unified_trading import HTTP
import pandas as pd
import logging

class RateLimitException(Exception):
    pass

class ExchangeManager:
    def __init__(self, config, logger):

        self.config = config
        self.logger = logger

        # Initialize Bybit HTTP Session
        self.session = HTTP(
            testnet=config['testnet'],
            api_key=config['api_key'],
            api_secret=config['api_secret'],
        )

    def get_candles(self, symbol, interval, limit=500):
        try:
            response = self.session.get_kline(
                category=self.config['category'],
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            if response['retCode'] == 0:
                data = response['result']['list']
                df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
                # Bybit returns klines in reverse chronological order
                df = df.iloc[::-1].reset_index(drop=True)
                return df
            else:
                self.logger.error(f"API Error getting candles: {response['retMsg']}")
                return None
        except Exception as e:
            if "rate limit" in str(e).lower():
                raise RateLimitException(e)
            self.logger.error(f"Exception getting candles: {e}")
            return None

    def get_balance(self):
        try:
            response = self.session.get_wallet_balance(
                accountType="UNIFIED",
                coin="USDT"
            )
            if response['retCode'] == 0:
                # Navigate to the available balance
                balances = response['result']['list'][0]['coin']
                for coin in balances:
                    if coin['coin'] == "USDT":
                        return float(coin['walletBalance'])
            return 0.0
        except Exception as e:
            if "rate limit" in str(e).lower():
                raise RateLimitException(e)
            self.logger.error(f"Exception getting balance: {e}")
            return 0.0

    def has_open_orders(self, symbol):
        try:
            response = self.session.get_open_orders(
                category=self.config['category'],
                symbol=symbol
            )
            if response['retCode'] == 0:
                return len(response['result']['list']) > 0
            return False
        except Exception as e:
            self.logger.error(f"Exception checking open orders: {e}")
            return False

    def get_position_size(self, symbol):
        try:
            response = self.session.get_positions(
                category=self.config['category'],
                symbol=symbol
            )
            if response['retCode'] == 0:
                positions = response['result']['list']
                for pos in positions:
                    if float(pos['size']) != 0:
                        return float(pos['size']), pos['side'], float(pos['avgPrice'])
            return 0.0, None, None
        except Exception as e:
            if "rate limit" in str(e).lower():
                raise RateLimitException(e)
            self.logger.error(f"Exception getting position: {e}")
            return 0.0, None, None

    def set_leverage(self, symbol, leverage):
        try:
            self.session.set_leverage(
                category=self.config['category'],
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
        except Exception as e:
            # Leverage might already be set, which can cause an error
            self.logger.info(f"Leverage update info: {e}")

    def get_instrument_info(self, symbol):
        try:
            response = self.session.get_instruments_info(
                category=self.config['category'],
                symbol=symbol
            )
            if response['retCode'] == 0:
                info = response['result']['list'][0]
                return {
                    'minOrderQty': float(info['lotSizeFilter']['minOrderQty']),
                    'qtyStep': float(info['lotSizeFilter']['qtyStep'])
                }
            return None
        except Exception as e:
            self.logger.error(f"Exception getting instrument info: {e}")
            return None

    def open_position(self, symbol, side, qty, sl, tp, order_type="Market", price=None):
        try:
            # Open Order
            params = {
                "category": self.config['category'],
                "symbol": symbol,
                "side": side,
                "orderType": order_type,
                "qty": str(qty),
                "takeProfit": str(round(tp, 4)),
                "stopLoss": str(round(sl, 4)),
                "tpTriggerBy": "LastPrice",
                "slTriggerBy": "LastPrice",
            }
            if order_type == "Limit":
                params["price"] = str(round(price, 4))

            order = self.session.place_order(**params)
            if order['retCode'] == 0:
                self.logger.info(f"Successfully opened {side} {order_type} position: {qty} {symbol} at {price if price else 'Market'}")
                return True
            else:
                self.logger.error(f"Order failed: {order['retMsg']}")
                return False
        except Exception as e:
            if "rate limit" in str(e).lower():
                raise RateLimitException(e)
            self.logger.error(f"Exception opening position: {e}")
            return False

    def close_partial_position(self, symbol, side, qty):
        try:
            # To close a position, we open an opposite order
            # If side is 'Buy', we need to 'Sell' to close
            close_side = "Sell" if side == "Buy" else "Buy"
            order = self.session.place_order(
                category=self.config['category'],
                symbol=symbol,
                side=close_side,
                orderType="Market",
                qty=str(qty),
                reduceOnly=True,
            )
            if order['retCode'] == 0:
                self.logger.info(f"Successfully closed partial position: {qty} {symbol}")
                return True
            else:
                self.logger.error(f"Partial close failed: {order['retMsg']}")
                return False
        except Exception as e:
            self.logger.error(f"Exception closing partial position: {e}")
            return False

    def update_stop_loss(self, symbol, sl_price):
        try:
            self.session.set_trading_stop(
                category=self.config['category'],
                symbol=symbol,
                stopLoss=str(round(sl_price, 4)),
            )
            self.logger.info(f"Stop loss updated to {sl_price} for {symbol}")
            return True
        except Exception as e:
            self.logger.error(f"Exception updating stop loss: {e}")
            return False
