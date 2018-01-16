import random
from decimal import Decimal as Dec
from typing import Optional, Tuple

from managers import HavvenManager as hm
from core import orderbook as ob
from .marketplayer import MarketPlayer


class Banker(MarketPlayer):
    """
    Wants to buy havvens and issue nomins, in order to accrue fees.
    They do this by constantly targeting copt with their issuance,
      burning and issuing as needed.

    If the banker is under collateralised (c_i > c_opt), first try to
      acquire more havvens using nomins generated by fees, and secondly,
      burn fiat to free up havvens.
    If the banker is over collateralised (c_i < c_opt), simply issue more nomins
      up to c_opt.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.collateralisation_diff: Dec = Dec('0.02')
        'how far off copt does the banker stay (percentage wise)'
        # step when initialised so nomins appear on the market.
        self.step()

    def setup(self, init_value: Dec):
        self.wage_parameter = init_value/Dec(100)
        init_value = init_value * Dec(random.random()/10 + 0.9)
        endowment = hm.round_decimal(init_value * Dec(4))
        self.fiat = init_value
        self.model.endow_havvens(self, endowment)

    def step(self) -> None:
        super().step()

        # spend excess fiat on havvens
        if self.available_fiat > self.issued_nomins:
            quantity = self.available_fiat - self.issued_nomins
            price = self.havven_fiat_market.lowest_ask_price()
            self.place_havven_fiat_bid_with_fee(quantity/price, price*Dec('1.05'))

        if self.collateralisation * (1 + self.collateralisation_diff) > self.model.mint.copt:
            # first try to buy more havvens with nomins
            if self.available_nomins > 0:
                havvens_needed = self.model.mint.havvens_off_optimal(self)
                price = self.havven_nomin_market.price_to_buy_quantity(havvens_needed)

                # only spend enough to get to havvens needed, or just buy as many as they can
                quantity = min(havvens_needed, self.available_nomins*price)

                trade = self.place_havven_nomin_bid_with_fee(price, quantity)
                if trade:
                    trade.cancel()  # don't hold onto this trade, as burning nomins next

            # if still under collateralised, burn nomins using fiat
            if self.collateralisation * (1 + self.collateralisation_diff) > self.model.mint.copt:
                nom_to_burn = self.model.mint.optimal_issuance_rights(self)
                if self.fiat < nom_to_burn:
                    raise Exception("not enough fiat to burn nomins to get to c_opt")
                self.free_havvens(nom_to_burn)

        elif self.collateralisation * (1 - self.collateralisation_diff) < self.model.mint.copt:
            to_escrow = self.model.mint.optimal_issuance_rights(self)
            self.escrow_havvens(to_escrow)
