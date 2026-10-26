import time
import yaml
import logging
import json
import os
import math
from logger import setup_logger
from exchange_manager import ExchangeManager, RateLimitException
from strategy import Strategy
from notifier import Notifier

STATE_FILE = "trade_state.json"

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def round_qty(qty, info):
    """Rounds quantity to match Bybit's qtyStep and minOrderQty."""
    step = info['qtyStep']
    min_qty = info['minOrderQty']
    rounded = round(qty / step) * step
    return max(rounded, min_qty)

def main():
    # 1. Load Config
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # 2. Initialize Components
    logger = setup_logger(config['log_file'])
    exch = ExchangeManager(config, logger)
    strat = Strategy(config)
    notifier = Notifier(config, logger)

    symbol = config['symbol']
    category = config['category']

    logger.info(f"Bot started for {symbol} | HTF: {config['htf']} | LTF: {config['ltf']}")
    notifier.send_message(f"🤖 <b>Bot Started</b>\nSymbol: {symbol}\nHTF: {config['htf']}m | LTF: {config['ltf']}m")

    # Load previous trade state
    state = load_state()

    while True:
        try:
            # Update Balance and Instrument Info
            balance = exch.get_balance()
            info = exch.get_instrument_info(symbol)
            if info is None:
                logger.error("Could not fetch instrument info. Skipping loop.")
                time.sleep(config['check_interval'])
                continue

            # Check if we have an active position on Bybit
            pos_size, pos_side, pos_avg_price = exch.get_position_size(symbol)

            if pos_size > 0:
                # --- POSITION MANAGEMENT ---
                logger.info(f"Active position: {pos_side} {pos_size} @ {pos_avg_price}")

                # Sync state with current position if not already tracked
                if not state or state.get('symbol') != symbol:
                    htf_df = exch.get_candles(symbol, config['htf'])
                    tp1, tp2 = strat.get_next_levels(htf_df, pos_avg_price, 'long' if pos_side == 'Buy' else 'short')
                    state = {
                        'symbol': symbol,
                        'side': pos_side,
                        'entry': pos_avg_price,
                        'qty': pos_size,
                        'sl': pos_avg_price * (0.98 if pos_side == 'Buy' else 1.02),
                        'tp1': tp1,
                        'tp2': tp2,
                        'tp1_hit': False
                    }
                    save_state(state)

                ltf_df = exch.get_candles(symbol, config['ltf'])
                current_price = ltf_df['close'].iloc[-1]

                side = state['side']
                sl = state['sl']
                tp1 = state['tp1']
                tp2 = state['tp2']
                tp1_hit = state['tp1_hit']

                # 1. Check Stop Loss
                if (side == 'Buy' and current_price <= sl) or (side == 'Sell' and current_price >= sl):
                    logger.info(f"Stop Loss hit at {current_price}")
                    exch.open_position(symbol, "Sell" if side == "Buy" else "Buy", pos_size, 0, 0, order_type="Market")
                    notifier.send_message(f"❌ <b>Stop Loss Hit</b>\nPrice: {current_price}\nResult: Loss")
                    state = {}
                    save_state(state)
                    continue

                # 2. Check TP1 (Partial Fixation)
                if not tp1_hit and tp1:
                    if (side == 'Buy' and current_price >= tp1) or (side == 'Sell' and current_price <= tp1):
                        logger.info(f"TP1 hit at {current_price}. Closing 50% and moving to BE.")
                        close_qty = round_qty(pos_size * 0.5, info)
                        if exch.close_partial_position(symbol, side, close_qty):
                            state['tp1_hit'] = True
                            state['sl'] = state['entry']
                            save_state(state)
                            notifier.send_message(f"🎯 <b>TP1 Hit!</b>\nPrice: {current_price}\n50% closed, SL moved to BE.")

                # 3. Check Trailing Stop
                if state.get('tp1_hit'):
                    trail_dist = current_price * config['trailing_stop_pct']
                    if side == 'Buy':
                        new_sl = current_price - trail_dist
                        if new_sl > state['sl']:
                            state['sl'] = new_sl
                            exch.update_stop_loss(symbol, state['sl'])
                            save_state(state)
                    else:
                        new_sl = current_price + trail_dist
                        if new_sl < state['sl']:
                            state['sl'] = new_sl
                            exch.update_stop_loss(symbol, state['sl'])
                            save_state(state)

                # 4. Check TP2
                if tp2:
                    if (side == 'Buy' and current_price >= tp2) or (side == 'Sell' and current_price <= tp2):
                        logger.info(f"TP2 hit at {current_price}. Closing remaining position.")
                        exch.open_position(symbol, "Sell" if side == "Buy" else "Buy", pos_size, 0, 0, order_type="Market")
                        notifier.send_message(f"🏆 <b>TP2 Hit!</b>\nPrice: {current_price}\nFull profit realized.")
                        state = {}
                        save_state(state)
                        continue

            else:
                # --- ENTRY SEARCH ---
                state = {}
                save_state(state)

                htf_df = exch.get_candles(symbol, config['htf'])
                ltf_df = exch.get_candles(symbol, config['ltf'])

                if htf_df is None or ltf_df is None:
                    time.sleep(config['check_interval'])
                    continue

                trend = strat.calculate_trend(htf_df)
                logger.info(f"Current Trend: {trend}")

                if trend != 'neutral':
                    sig = strat.check_entry_signal(ltf_df, htf_df, trend)
                    if sig:
                        side, entry, sl, tp = sig
                        logger.info(f"Signal found: {side} at {entry}, SL: {sl}, TP: {tp}")

                        risk_amount = balance * config['risk_per_trade']
                        sl_dist = abs(entry - sl)

                        if sl_dist == 0:
                            continue

                        raw_qty = risk_amount / sl_dist
                        qty = round_qty(raw_qty, info)

                        if qty < info['minOrderQty']:
                            logger.info(f"Calculated qty {qty} below minOrderQty. Skipping.")
                            continue

                        if exch.open_position(symbol, side, qty, sl, tp, order_type="Limit", price=entry):
                            tp1, tp2 = strat.get_next_levels(htf_df, entry, trend)
                            state = {
                                'symbol': symbol,
                                'side': side,
                                'entry': entry,
                                'qty': qty,
                                'sl': sl,
                                'tp1': tp1,
                                'tp2': tp2,
                                'tp1_hit': False
                            }
                            save_state(state)
                            notifier.send_message(f"🚀 <b>Entry Order Placed</b>\nSide: {side}\nPrice: {entry}\nQty: {qty}\nSL: {sl}\nTP1: {tp1}")

        except RateLimitException as e:
            logger.warning(f"Rate limit hit: {e}. Sleeping for 60s...")
            time.sleep(60)
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
            time.sleep(config['check_interval'])

        time.sleep(config['check_interval'])

if __name__ == "__main__":
    main()
