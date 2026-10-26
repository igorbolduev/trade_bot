import pandas as pd
import yaml
import logging
from logger import setup_logger
from exchange_manager import ExchangeManager
from strategy import Strategy

def main():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    logger = setup_logger(config['log_file'])
    logger.info("Starting Professional Backtest with Trailing Stop...")

    exch = ExchangeManager(config, logger)
    strat = Strategy(config)

    symbol = config['symbol']
    htf_df = exch.get_candles(symbol, config['htf'], limit=1000)
    ltf_df = exch.get_candles(symbol, config['ltf'], limit=1000)

    if htf_df is None or ltf_df is None:
        logger.error("No data")
        return

    balance = 1000.0
    initial_balance = balance
    position = None 
    trades = []

    start_idx = max(config['sma_period'], config['adx_period'])

    for i in range(start_idx, len(ltf_df)):
        candle = ltf_df.iloc[i]
        price = candle['close']
        time = candle['timestamp']
        htf_slice = htf_df[htf_df['timestamp'] <= time]

        if position:
            hi, lo = candle['high'], candle['low']
            if position['side'] == 'Buy':
                if lo <= position['sl']:
                    qty = position['qty'] if not position['tp1_hit'] else position['qty'] * 0.5
                    balance += (position['sl'] - position['entry']) * qty
                    trades.append({'result': 'Loss'})
                    position = None
                    continue
                if not position['tp1_hit'] and position['tp1'] and hi >= position['tp1']:
                    balance += (position['tp1'] - position['entry']) * (position['qty'] * 0.5)
                    position['tp1_hit'] = True
                    position['sl'] = position['entry']
                if position and position['tp1_hit']:
                    trail = price * (1 - config.get('trailing_stop_pct', 0))
                    position['sl'] = max(position['sl'], trail)
                if position and position['tp2'] and hi >= position['tp2']:
                    qty = position['qty'] * 0.5 if position['tp1_hit'] else position['qty']
                    balance += (position['tp2'] - position['entry']) * qty
                    trades.append({'result': 'Win'})
                    position = None
                    continue
            else: # Sell
                if hi >= position['sl']:
                    qty = position['qty'] if not position['tp1_hit'] else position['qty'] * 0.5
                    balance += (position['entry'] - position['sl']) * qty
                    trades.append({'result': 'Loss'})
                    position = None
                    continue
                if not position['tp1_hit'] and position['tp1'] and lo <= position['tp1']:
                    balance += (position['entry'] - position['tp1']) * (position['qty'] * 0.5)
                    position['tp1_hit'] = True
                    position['sl'] = position['entry']
                if position and position['tp1_hit']:
                    trail = price * (1 + config.get('trailing_stop_pct', 0))
                    position['sl'] = min(position['sl'], trail)
                if position and position['tp2'] and lo <= position['tp2']:
                    qty = position['qty'] * 0.5 if position['tp1_hit'] else position['qty']
                    balance += (position['entry'] - position['tp2']) * qty
                    trades.append({'result': 'Win'})
                    position = None
                    continue

        if position is None:
            trend = strat.calculate_trend(htf_slice)
            sig = strat.check_entry_signal(ltf_df.iloc[:i+1], htf_slice, trend)
            if sig:
                side, entry, sl, tp = sig
                qty = (balance * config['risk_per_trade']) / abs(entry - sl)
                tp1, tp2 = strat.get_next_levels(htf_slice, entry, trend)
                position = {'side': side, 'entry': entry, 'qty': qty, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'tp1_hit': False}

    total_trades = len(trades)
    wins = len([t for t in trades if t['result'] == 'Win'])
    print(f"\nFinal Balance: {balance:.2f} USDT | PnL: {balance-initial_balance:.2f} | Trades: {total_trades} | WinRate: {(wins/total_trades*100 if total_trades>0 else 0):.2f}%")

if __name__ == "__main__":
    main()