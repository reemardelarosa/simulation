"""model.py: The Havven model itself lives here."""

from typing import Dict, Optional
from decimal import Decimal as Dec

from mesa import Model
from mesa.time import RandomActivation

import stats
import agents as ag
from managers import (HavvenManager, MarketManager,
                      FeeManager, Mint,
                      AgentManager)


class HavvenModel(Model):
    """
    An agent-based model of the Havven stablecoin system. This class will
      provide the basic market functionality of Havven, an exchange, and a
      place for the market agents to live and interact.
    The aim is to stabilise the nomin price, but we would also like to measure
      other quantities including liquidity, volatility, wealth concentration,
      velocity of money and so on.
    """

    def __init__(self, num_agents: int, init_value: float = 1000.0,
                 utilisation_ratio_max: float = 1.0,
                 match_on_order: bool = True,
                 agent_fractions: Optional[Dict[str, int]] = None,
                 agent_minimum: int = 1) -> None:
        # Mesa setup.

        super().__init__()

        # The schedule will activate agents in a random order per step.
        self.schedule = RandomActivation(self)

        # Set up data collection.
        self.datacollector = stats.create_datacollector()

        # Initialise simulation managers.
        self.manager = HavvenManager(Dec(utilisation_ratio_max), match_on_order)
        self.fee_manager = FeeManager(self.manager)
        self.market_manager = MarketManager(self.manager, self.fee_manager)
        self.mint = Mint(self.manager, self.market_manager)

        if agent_fractions is None:
            agent_fractions = {
                'Banker': 0.2,
                'Arbitrageur': 0.2,
                'Randomizer': 0.3,
                'NominShorter': 0.15,
                'HavvenEscrowNominShorter': 0.15
            }

        self.agent_manager = AgentManager(self, num_agents,
                                          agent_fractions, Dec(init_value),
                                          agent_minimum=agent_minimum)

    def fiat_value(self, havvens=Dec('0'), nomins=Dec('0'),
                   fiat=Dec('0')) -> Dec:
        """Return the equivalent fiat value of the given currency basket."""
        return self.market_manager.havvens_to_fiat(havvens) + \
            self.market_manager.nomins_to_fiat(nomins) + fiat

    def endow_havvens(self, agent: ag.MarketPlayer, havvens: Dec) -> None:
        """Grant an agent an endowment of havvens."""
        if havvens > 0:
            value = min(self.manager.havvens, havvens)
            agent.havvens += value
            self.manager.havvens -= value

    def step(self) -> None:
        """Advance the model by one step."""
        # Agents submit trades.
        self.schedule.step()

        self.market_manager.havven_nomin_market.step_history()
        self.market_manager.havven_fiat_market.step_history()
        self.market_manager.nomin_fiat_market.step_history()

        # Resolve outstanding trades.
        if not self.manager.match_on_order:
            self.market_manager.havven_nomin_market.match()
            self.market_manager.havven_fiat_market.match()
            self.market_manager.nomin_fiat_market.match()

        # Distribute fees periodically.
        if (self.manager.time % self.fee_manager.fee_period) == 0:
            self.fee_manager.distribute_fees(self.schedule.agents)

        # Collect data.
        self.datacollector.collect(self)

        # Advance Time Itself.
        self.manager.time += 1
