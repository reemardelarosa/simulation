import random

import numpy as np
from scipy.stats import skewnorm

from mesa import Agent, Model
from mesa.time import RandomActivation
from mesa.space import MultiGrid
from mesa.datacollection import DataCollector

import orderbook


class MarketPlayer(Agent):
    """
    An agent with a fixed initial wealth in fiat, with which it must buy into the market.
    The agent may escrow curits in order to issue nomins, and use various strategies in order
    to trade in the marketplace. Its aim is to increase its own wealth.
    """

    def __init__(self, unique_id, model, endowment):
        super().__init__(unique_id, model)
        self.fiat = endowment 
        self.curits = 0
        self.nomins = 0
        self.escrowed_curits = 0
        self.issued_nomins = 0

        self.orders = set()

    def wealth(self) -> float:
        """Return the total wealth of this agent at current fiat prices."""
        return self.fiat + \
               self.model.cur_to_fiat(self.curits + self.escrowed_curits) + \
               self.model.nom_to_fiat(self.nomins - self.issued_nomins)

    def transfer_fiat_to(self, recipient:"MarketPlayer", value:float) -> bool:
        """Transfer a positive value of fiat to the recipient, if balance is sufficient. Return True on success."""
        return self.model.transfer_fiat(self, recipient, value)
    
    def transfer_curits_to(self, recipient:"MarketPlayer", value:float) -> bool:
        """Transfer a positive value of curits to the recipient, if balance is sufficient. Return True on success."""
        return self.model.transfer_curits(self, recipient, value)

    def transfer_nomins_to(self, recipient:"MarketPlayer", value:float) -> bool:
        """Transfer a positive value of nomins to the recipient, if balance is sufficient. Return True on success."""
        return self.model.transfer_nomins(self, recipient, value)
    
    def escrow_curits(self, value:float) -> bool:
        """Escrow a positive value of curits in order to be able to issue nomins against them."""
        if self.curits >= value >= 0:
            self.curits -= value
            self.escrowed_curits += value
            self.model.escrowed_curits += value
            return True
        return False

    def available_escrowed_curits(self) -> float:
        """Return the quantity of escrowed curits which is not locked by issued nomins (may be negative)."""
        return self.escrowed_curits - self.model.nom_to_cur(self.issued_nomins)

    def unavailable_escrowed_curits(self) -> float:
        """Return the quantity of escrowed curits which is locked by having had nomins issued against it (may be greater than total escrowed curits)."""
        return self.model.nom_to_cur(self.issued_nomins)

    def unescrow_curits(self, value:float) -> bool:
        """Unescrow a quantity of curits, if there are not too many issued nomins locking it."""
        if 0 <= value <= available_escrowed_curits(value):
            self.curits += value
            self.escrowed_curits -= value
            return True
        return False

    def issuance_rights(self) -> float:
        """The total quantity of nomins this agent has a right to issue."""
        return self.model.cur_to_nom(self.escrowed_curits) * self.model.utilisation_ratio_max

    def issue_nomins(self, value:float) -> bool:
        """Issue a positive value of nomins against currently escrowed curits, up to the utilisation ratio maximum."""
        remaining = self.issuance_rights() - self.issued_nomins
        if 0 <= value <= remaining:
            self.issued_nomins += value
            self.nomins += value
            return True
        return False

    def burn_nomins(self, value:float) -> bool:
        """Burn a positive value of issued nomins, which frees up curits."""
        if 0 <= value <= self.nomins and value <= self.issued_nomins:
            self.nomins -= value
            self.issued_nomins -= value
            return True
        return False

    def cancel_order(self, order):
        if order in self.orders:
            self.orders.remove(order)

    def step(self) -> None:
        pass


# Functions for extracting aggregate information from the Havven model.

def wealth_sd(model:"HavvenModel") -> float:
    """Return the standard deviation of wealth in the economy."""
    num_agents = len(model.schedule.agents)
    wealths = [a.wealth() for a in model.schedule.agents]
    mean_wealth = sum(wealths)/num_agents
    sum_squared_diffs = sum([(w - mean_wealth)**2 for w in wealths])
    return (sum_squared_diffs/(num_agents - 1))**0.5

def gini(model:"HavvenModel") -> float:
    """Return the gini coefficient in the economy."""
    n, s_wealth = len(model.schedule.agents), sorted([a.wealth() for a in model.schedule.agents])
    return 1 + (1/n) - 2*(sum(x*(n-i) for i, x in enumerate(s_wealth)) / (n*sum(s_wealth)))

def max_wealth(model:"HavvenModel") -> float:
    """Return the wealth of the richest person in the economy."""
    w = [a.wealth() for a in model.schedule.agents]
    return max(w)

def min_wealth(model:"HavvenModel") -> float:
    """Return the wealth of the poorest person in the economy."""
    w = [a.wealth() for a in model.schedule.agents]
    return min(w)

class HavvenModel(Model):
    """
    An agent-based model of the Havven stablecoin system. This class will provide the basic
    market functionality of havven, an exchange, and a place for the market agents to live and
    interact.
    The aim is to stabilise the nomin price, but we would also like to measure other quantities
    including liquidity, volatility, wealth concentration, velocity of money and so on.
    """

    def __init__(self, N, max_endowment=1000):
        # Mesa setup
        self.running = True
        self.schedule = RandomActivation(self)
        self.collector = DataCollector(model_reporters={"Gini": gini,
                                                        "Wealth SD": wealth_sd,
                                                        "Max Wealth": max_wealth,
                                                        "Min Wealth": min_wealth},
                                       agent_reporters={"Wealth": lambda a: a.wealth})
        self.time = 1

        # Add the market participants
        self.num_agents = N
        for i in range(self.num_agents):
            endowment = int(skewnorm.rvs(100)*max_endowment)
            a = MarketPlayer(i, self, endowment)
            self.schedule.add(a)

        # Market variables

        # Prices in fiat per token
        self.curit_price = 1.0
        self.nomin_price = 1.0

        # Money Supply
        self.curit_supply = 10.0**9
        self.nomin_supply = 0.0
        self.escrowed_curits = 0.0
        self.issued_nomins = 0.0
        
        # Havven's own capital supplies
        self.curits = self.curit_supply
        self.nomins = 0

        # Fees
        self.fee_period = 100
        self.nom_transfer_fee_rate = 0.005
        self.cur_transfer_fee_rate = 0.01
        # TODO: charge issuance and redemption fees
        self.issuance_fee_rate = 0.01
        self.redemption_fee_rate = 0.02
        
        # TODO: Move fiat fees and currency pool into its own object
        self.fiat_transfer_fee_rate = 0.01

        # Utilisation Ratio maximum (between 0 and 1)
        self.utilisation_ratio_max = 1.0

        # Order books
        # If a book is X_Y_market, then buyers hold X and sellers hold Y.
        self.cur_nom_market = orderbook.OrderBook(self.cur_nom_match)
        self.cur_fiat_market = orderbook.OrderBook(self.cur_fiat_match)
        self.nom_fiat_market = orderbook.OrderBook(self.nom_fiat_match)
    
    def __x_y_transfer__(self, bid, ask, x_success, y_success, x_transfer, y_transfer) -> bool:
        """
        Trade between the given ask and bid if they can, with the given transfer and success functions.
        Cancel any orders which the agent cannot afford to service.
        """
        if ask.price > bid.price:
            return False
        
        # Price will be favourable to whoever went first.
        # The earlier poster does no worse than their posted price, but may do better;
        # while the later poster transacts at their posted price.
        price = ask.price if ask.time > bid.time else bid.price
        quantity = min(ask.quantity, bid.quantity)
        buy_val = quantity*price

        # Only perform the actual transfer if it would be successful.
        # Cancel any orders that would not succeed.
        fail = False
        if not x_success(bid.issuer, ask.issuer, buy_val):
            bid.cancel()
            fail = True
        if not y_success(ask.issuer, bid.issuer, quantity):
            ask.cancel()
            fail = True
        if fail:
            return False
        
        # Perform the actual transfers
        x_transfer(bid.issuer, ask.issuer, buy_val)
        y_transfer(ask.issuer, bid.issuer, quantity)

        # Update the orders, and cancel any with 0 remaining quantity.
        ask.quantity -= quantity
        bid.quantity -= quantity
        if ask.quantity == 0:
            ask.cancel()
        if bid.quantity == 0:
            bid.cancel()

        return True

    def cur_nom_match(self, bid, ask) -> bool:
        """Buyer offers curits in exchange for nomins from the seller."""
        return self.__x_y_match__(bid, ask,
                                  self.transfer_curits_success,
                                  self.transfer_nomins_success,
                                  self.transfer_curits,
                                  self.transfer_nomins)

    def cur_fiat_match(self, buyer:MarketPlayer, seller:MarketPlayer, buy_val:float, sell_val:float) -> bool:
        """Buyer offers curits in exchange for fiat from the seller."""
        return self.__x_y_match__(bid, ask,
                                  self.transfer_curits_success,
                                  self.transfer_fiat_success,
                                  self.transfer_curits,
                                  self.transfer_fiat)

    def nom_fiat_match(self, buyer:MarketPlayer, seller:MarketPlayer, buy_val:float, sell_val:float) -> bool:
        """Buyer offers nomins in exchange for fiat from the seller."""
        return self.__x_y_match__(bid, ask,
                                  self.transfer_nomins_success,
                                  self.transfer_fiat_success,
                                  self.transfer_nomins,
                                  self.transfer_fiat)

    def transfer_fiat_fee(self, value):
        return value * self.fiat_transfer_fee

    def transfer_curits_fee(self, value):
        return value * self.cur_transfer_fee

    def transfer_nomins_fee(self, value):
        return value * self.nom_transfer_fee

    def transfer_fiat_success(self, sender:MarketPlayer, value:float) -> bool:
        """True iff the sender could successfully send a value of fiat."""
        return 0 <= value <= sender.fiat
    
    def transfer_curits_success(self, sender:MarketPlayer, value:float) -> bool:
        """True iff the sender could successfully send a value of curits."""
        return 0 <= value + self.transfer_curits_fee(value) <= sender.fiat
    
    def transfer_nomins_success(self, sender:MarketPlayer, value:float) -> bool:
        """True iff the sender could successfully send a value of nomins."""
        return 0 <= value + self.transfer_nomins_fee(value) <= sender.fiat

    def transfer_fiat(self, sender:MarketPlayer, recipient:MarketPlayer, value:float) -> bool:
        """Transfer a positive value of fiat currency from the sender to the recipient, if balance is sufficient.
        Return True on success."""
        if self.transfer_fiat_success(sender, value):
            sender.fiat -= value
            recipient.fiat += value
            return True
        return False
    
    def transfer_curits(self, sender:MarketPlayer, recipient:MarketPlayer, value:float) -> bool:
        """Transfer a positive value of curits from the sender to the recipient, if balance is sufficient.
        Return True on success."""
        if self.transfer_curits_success(sender, value):
            fee = self.transfer_curits_fee(value)
            sender.curits -= value + fee
            recipient.curits += value
            self.curits += fee
            return True
        return False
    
    def transfer_nomins(self, sender:MarketPlayer, recipient:MarketPlayer, value:float) -> bool:
        """Transfer a positive value of nomins from the sender to the recipient, if balance is sufficient.
        Return True on success."""
        if self.transfer_nomins_success(sender, value):
            fee = self.transfer_nomins_fee(value)
            sender.nomins -= value + fee
            recipient.nomins += value
            self.nomins += fee
            return True
        return False 

    def cur_to_nom(self, value:float) -> float:
        """Convert a quantity of curits to its equivalent value in nomins."""
        return (value * self.curit_price) / self.nomin_price
    
    def cur_to_fiat(self, value:float) -> float:
        """Convert a quantity of curits to its equivalent value in fiat."""
        return value * self.curit_price
    
    def nom_to_cur(self, value:float) -> float:
        """Convert a quantity of nomins to its equivalent value in curits."""
        return (value * self.nomin_price) / self.curit_price

    def nom_to_fiat(self, value:float) -> float:
        """Convert a quantity of nomins to its equivalent value in fiat."""
        return value * self.nomin_price

    def fiat_to_cur(self, value:float) -> float:
        """Convert a quantity of fiat to its equivalent value in curits."""
        return value / self.curit_price

    def fiat_to_nom(self, value:float) -> float:
        """Convert a quantity of fiat to its equivalent value in nomins."""
        return value / self.nomin_price

    def distribute_fees(self):
        """Distribute currently held nomins to holders of curits."""
        # Different fee modes:
        #  * distributed by held curits
        # TODO: * distribute by escrowed curits
        # TODO: * distribute by issued nomins
        # TODO: * distribute by motility

        # Held curits
        unit = self.nomins / self.curit_supply
        for agent in self.schedule.agents:
            if self.nomins == 0:
                break
            qty = min(agent.curits * unit, self.nomins)
            agent.nomins += qty
            self.nomins -= qty
        
    def step(self) -> None:
        """Advance the model by one step."""
        # Agents submit trades
        self.schedule.step()

        # Resolve outstanding trades
        self.cur_nom_market.resolve()
        self.cur_fiat_market.resolve()
        self.nom_fiat_market.resolve()

        # Distribute fees periodically.
        if (self.time % self.fee_period) == 0:
            self.distribute_fees()

        # Collect data
        self.collector.collect(self)
        self.time += 1