# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2018 reverendus
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import logging
import sys

from web3 import Web3, HTTPProvider

from auction_keeper.external_model import ExternalModelFactory
from auction_keeper.gas import UpdatableGasPrice
from auction_keeper.logic import Auction, ModelInput, Auctions, ModelOutput
from auction_keeper.strategy import FlopperStrategy
from pymaker import Address, Wad
from pymaker.approval import directly
from pymaker.auctions import Flopper
from pymaker.gas import FixedGasPrice, DefaultGasPrice
from pymaker.lifecycle import Lifecycle


class AuctionKeeper:
    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='auction-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--flopper", type=str, required=True,
                            help="Ethereum address of the Flopper contract")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)

        #TODO mutually exclusive flipper|flapper|flopper
        self.flopper = Flopper(web3=self.web3, address=Address(self.arguments.flopper))

        self.strategy = FlopperStrategy(self.flopper)

        self.auctions = Auctions(flipper=None, flapper=None, flopper=self.flopper.address, model_factory=ExternalModelFactory())

        logging.basicConfig(format='%(asctime)-15s %(levelname)-8s %(message)s',
                            level=(logging.DEBUG if self.arguments.debug else logging.INFO))

    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.on_startup(self.startup)
            lifecycle.on_block(self.check_all_auctions)

    def startup(self):
        self.approve()

    def approve(self):
        self.flopper.approve(directly())

    def check_all_auctions(self):
        for auction_id in range(1, self.flopper.kicks()+1):
            self.check_auction(auction_id)

    def check_auction(self, auction_id: int):
        assert(isinstance(auction_id, int))

        # Read our auction state
        auction = self.auctions.get_auction(auction_id)
        #TODO detecting auctions which are gone. not recreating them

        with auction.lock:
            # Read auction information
            input = self.strategy.get_input(auction_id)

            # Feed the model with current state
            auction.feed_model(input)

            # Check if the auction is finished.
            # If it is finished and we are the winner, `deal` the auction.
            # If it is finished and we aren't the winner, there is no point in carrying on with this auction.
            auction_finished = (input.tic < self.flopper.era() and input.tic != 0) or (input.end < self.flopper.era())

            if auction_finished:
                if input.guy == self.our_address:
                    # TODO this should happen asynchronously

                    # Always using default gas price for `deal`
                    self.flopper.deal(auction_id).transact(gas_price=DefaultGasPrice())

            if not auction_finished:
                if input.guy != self.our_address:
                    # Check if we can bid.
                    # If we can, bid.
                    auction_price = input.bid / input.lot
                    auction_price_min_increment = auction_price * self.flopper.beg()

                    output = auction.model_output()
                    if output is not None:
                        our_price = output.price
                        if our_price >= auction_price_min_increment:
                            our_lot = input.bid / our_price

                            gas_price = UpdatableGasPrice(output.gas_price)

                            # TODO this should happen asynchronously
                            self.flopper.dent(auction_id, our_lot, input.bid).transact(gas_price=gas_price)


if __name__ == '__main__':
    AuctionKeeper(sys.argv[1:]).main()
