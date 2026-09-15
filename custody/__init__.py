"""Offline-verified contracts and custody runtime; no built-in live broker."""
from .models import JobRequest, Contract, Session, Quote, Frame, OrderUpdate
from .marketdata import Bar, MarketDataProvider, MissingCustodyPairError, require_paired_bars
from .pnl import (OPTION_MULTIPLIER, CustodyMetricError, assert_custody_role,
                  custody_case_pnl, option_pnl, option_return)
from .registry import Registry
from .offline import OfflineMarket, assert_paired_slice
from .service import CustodyService, ExecutionPolicy
from .signals import SignalProvider
from .controller import Controller
