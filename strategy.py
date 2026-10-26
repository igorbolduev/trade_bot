import pandas as pd
import pandas_ta as ta
import numpy as np

class Strategy:
    def __init__(self, config):
        self.config = config

    def calculate_trend(self, htf_df):
        """
        Determines the current trend based on SMA and ADX.
        """
        if len(htf_df) < max(self.config['sma_period'], self.config['adx_period']):
            return 'neutral'

        sma = ta.sma(htf_df['close'], length=self.config['sma_period'])
        adx_df = ta.adx(htf_df['high'], htf_df['low'], htf_df['close'], length=self.config['adx_period'])

        current_price = htf_df['close'].iloc[-1]
        current_sma = sma.iloc[-1]
        current_adx = adx_df['ADX_14'].iloc[-1] if 'ADX_14' in adx_df.columns else None

        if current_adx is None or current_adx <= self.config['adx_threshold']:
            return 'neutral'

        if current_price > current_sma:
            return 'long'
        elif current_price < current_sma:
            return 'short'

        return 'neutral'

    def find_levels(self, df, window=20):
        """
        Finds significant support and resistance levels based on local peaks/troughs.
        """
        levels = []
        for i in range(window, len(df) - window):
            # Local maximum (Resistance)
            if df['high'].iloc[i] == max(df['high'].iloc[i-window:i+window+1]):
                levels.append(df['high'].iloc[i])
            # Local minimum (Support)
            if df['low'].iloc[i] == min(df['low'].iloc[i-window:i+window+1]):
                levels.append(df['low'].iloc[i])

        # Cluster levels to avoid having 100 lines close to each other
        if not levels:
            return []

        unique_levels = []
        sorted_levels = sorted(levels)
        if sorted_levels:
            unique_levels.append(sorted_levels[0])
            for i in range(1, len(sorted_levels)):
                if sorted_levels[i] - unique_levels[-1] > (df['close'].iloc[-1] * 0.002): # 0.2% gap
                    unique_levels.append(sorted_levels[i])

        return unique_levels

    def detect_setup(self, df):
        """
        Detects Price Action setups on the last closed candle.
        Returns: ('pin_bar', 'inside_bar', 'outside_bar', 'fake') or None
        """
        if len(df) < 2:
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        body = abs(curr['close'] - curr['open'])
        candle_range = curr['high'] - curr['low']
        if candle_range == 0: return None

        # 1. Pin Bar
        # Long wick, small body. Wick should be at least 2x body.
        upper_wick = curr['high'] - max(curr['open'], curr['close'])
        lower_wick = min(curr['open'], curr['close']) - curr['low']

        if upper_wick > body * 2 and upper_wick > lower_wick:
            return 'pin_bar_bearish'
        if lower_wick > body * 2 and lower_wick > upper_wick:
            return 'pin_bar_bullish'

        # 2. Inside Bar
        if curr['high'] < prev['high'] and curr['low'] > prev['low']:
            return 'inside_bar'

        # 3. Outside Bar
        if curr['high'] > prev['high'] and curr['low'] < prev['low']:
            return 'outside_bar'

        # 4. Fake (Spring/Upthrust)
        # Price briefly breaks a level but closes back inside.
        # (This is handled better in check_entry_signal combined with level)

        return None

    def check_entry_signal(self, ltf_df, htf_df, trend):
        """
        New logic: Trend -> Level -> Retest -> Setup -> 50% Entry
        Returns: (side, entry_price, stop_loss, take_profit) or None
        """
        if trend == 'neutral':
            return None

        levels = self.find_levels(htf_df)
        if not levels:
            return None

        curr_price = ltf_df['close'].iloc[-1]

        # Find the closest level
        closest_level = min(levels, key=lambda x: abs(x - curr_price))
        dist_to_level = abs(curr_price - closest_level) / curr_price

        # 1. Check for Retest: Price must be near a level (within 0.2%)
        if dist_to_level > 0.002:
            return None

        # 2. Check for Setup on the last closed candle
        setup = self.detect_setup(ltf_df)
        if not setup:
            return None

        # We only trade setups that align with the trend
        # For Long: Setup must be bullish (or neutral like inside bar) and at a support level
        # For Short: Setup must be bearish (or neutral like inside bar) and at a resistance level

        candle = ltf_df.iloc[-1]
        c_high = candle['high']
        c_low = candle['low']
        c_range = c_high - c_low

        if trend == 'long':
            # Support level retest + Bullish setup
            if setup in ['pin_bar_bullish', 'inside_bar', 'outside_bar']:
                entry_price = c_low + (c_range * 0.5) # 50% of the setup candle
                stop_loss = c_low * 0.998 # Stop slightly below extreme
                take_profit = entry_price * (1 + self.config['take_profit_pct'])
                return ('Buy', entry_price, stop_loss, take_profit)

        elif trend == 'short':
            # Resistance level retest + Bearish setup
            if setup in ['pin_bar_bearish', 'inside_bar', 'outside_bar']:
                entry_price = c_high - (c_range * 0.5) # 50% of the setup candle
                stop_loss = c_high * 1.002 # Stop slightly above extreme
                take_profit = entry_price * (1 - self.config['take_profit_pct'])
                return ('Sell', entry_price, stop_loss, take_profit)

        return None

    def get_next_levels(self, df, current_price, trend):
        """
        Returns the next two significant levels in the direction of the trend.
        For Long: levels above current_price.
        For Short: levels below current_price.
        """
        levels = self.find_levels(df)
        if not levels:
            return None, None

        if trend == 'long':
            above = sorted([l for l in levels if l > current_price])
            tp1 = above[0] if len(above) >= 1 else None
            tp2 = above[1] if len(above) >= 2 else None
            return tp1, tp2
        elif trend == 'short':
            below = sorted([l for l in levels if l < current_price], reverse=True)
            tp1 = below[0] if len(below) >= 1 else None
            tp2 = below[1] if len(below) >= 2 else None
            return tp1, tp2

        return None, None

