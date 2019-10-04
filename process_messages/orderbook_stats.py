#!/usr/bin/env python3
"""
"""

# standard libraries
from collections import Counter, defaultdict, namedtuple
from operator import neg, itemgetter
from pathlib import Path
import struct

# third-party packages
import numpy as np
import pandas as pd
from sortedcontainers import SortedDict


class OrderBookSide(SortedDict):
    def __missing__(self, key):
        return 0
    def peekitem(self, index=-1):
        try:
            return super().peekitem(index)
        except IndexError:
            return (np.nan, np.nan)


class SingleDayIMIData(object):
    """Class that loads and processes IMI messages for a single date"""

    def __init__(self, file_path: Path):
        self.date = file_path.name[11:21].replace("_", "-")

        self.file_path = file_path
        # Reading the binary file into memory
        with open(self.file_path, "rb") as binary_file:
            self.data = binary_file.read()
        self.number_of_bytes = len(self.data)
        self.current_position = 0

        self.unpack = struct.unpack
        self.get_order_info = itemgetter(
            "orderbook_no", "book_side", "price", "quantity_outstanding"
        )

        self.orders = defaultdict(dict)
        self.orderbooks = defaultdict(dict)
        self.price_tick_sizes = defaultdict(dict)
        self.metadata = defaultdict(dict)
        self.snapshots = defaultdict(dict)

        self.transactions = defaultdict(list)
        self.best_bid_ask = defaultdict(list)
        self.best_depths = defaultdict(list)
        self.trading_actions = defaultdict(list)
        self.blue_chip_orderbooks = list()

        self.Transaction = namedtuple("Transaction", ["timestamp", "price", "size",
            "best_bid", "best_ask", "best_bid_quantity", "best_ask_quantity"])
        self.Snapshot = namedtuple("Snapshot", ["best_bid", "best_ask",
            "best_bid_quantity", "best_ask_quantity"])
        self.NewBestPrice = namedtuple("NewBestPrice", ["timestamp", "book_side", "new_best_price"])
        self.NewBestQuantity = namedtuple("NewBestQuantity", ["timestamp", "book_side", "new_best_quantity"])


    def process_messages(self):
        """Convert and process all messages inside a loop"""

        # as long as we haven't reached the end of the file:
        while self.current_position < self.number_of_bytes:

            message_length = self.data[self.current_position + 1]
            message_type = self.data[
                self.current_position + 2 : self.current_position + 3
            ]
            message_start = self.current_position + 3
            message_end = self.current_position + message_length + 2
            # access the message
            message = self.data[message_start:message_end]

            # Add Order Message
            if message_type == b"A":
                message = self.unpack(">iqsiii", message)
                timestamp = self.microseconds + int(message[0] * 1e-3)
                order_no = message[1]
                book_side = message[2]
                quantity = message[3]
                orderbook_no = message[4]
                price = message[5]
                this_order = self.orders[order_no]
                this_order["orderbook_no"] = orderbook_no
                this_order["book_side"] = book_side
                this_order["quantity_outstanding"] = quantity
                this_order["price"] = price

                if orderbook_no in self.blue_chip_orderbooks:
                    # update the side of the orderbook_no
                    this_orderbook = self.orderbooks[orderbook_no][book_side]
                    this_orderbook[price] += quantity
                    # record if price was at best
                    best_price, best_quantity = this_orderbook.peekitem(0)
                    if price == best_price:
                        self.best_depths[orderbook_no].append(self.NewBestQuantity(
                            timestamp=timestamp,
                            book_side=book_side,
                            new_best_quantity=best_quantity)
                        )
                        # if it's the only one at the best price
                        if quantity == best_quantity:
                            self.best_bid_ask[orderbook_no].append(self.NewBestPrice(
                                timestamp=timestamp,
                                book_side=book_side,
                                new_best_price=price)
                            )

            # Time Stamp – Seconds message
            elif message_type == b"T":
                message = self.unpack(">i", message)
                seconds = message[0]
                self.microseconds = int(seconds * 1e6)
                if seconds >= 8 * 3600 and seconds < 18 * 3600:
                    for orderbook_no in self.blue_chip_orderbooks:
                        this_orderbook = self.orderbooks[orderbook_no]
                        best_bid_price, best_bid_quantity = this_orderbook[b"B"].peekitem(0)
                        best_ask_price, best_ask_quantity = this_orderbook[b"S"].peekitem(0)
                        self.snapshots[orderbook_no][seconds] = self.Snapshot(
                            best_bid=best_bid_price,
                            best_ask=best_ask_price,
                            best_bid_quantity=best_bid_quantity,
                            best_ask_quantity=best_ask_quantity,
                        )

            # Order Delete Message
            elif message_type == b"D":
                message = self.unpack(">iq", message)
                timestamp = self.microseconds + int(message[0] * 1e-3)
                order_no = message[1]
                this_order = self.orders[order_no]
                self.orders.pop(order_no)

                # update the order book
                orderbook_no, book_side, price, quantity_outstanding = self.get_order_info(
                    this_order
                )
                if orderbook_no in self.blue_chip_orderbooks:
                    this_orderbook = self.orderbooks[orderbook_no][book_side]
                    this_orderbook[price] -= quantity_outstanding
                    best_price, best_quantity = this_orderbook.peekitem(0)
                    if price == best_price:
                        if best_quantity == 0:
                            # if there is no quantity left at that price, we remove
                            # this price level and note that there's a new best price
                            this_orderbook.pop(price)
                            best_price, best_quantity = this_orderbook.peekitem(0)
                            self.best_bid_ask[orderbook_no].append(self.NewBestPrice(
                                timestamp=timestamp,
                                book_side=book_side,
                                new_best_price=best_price)
                            )
                        # in any case, if the price was at best, we note the new best quantity
                        self.best_depths[orderbook_no].append(self.NewBestQuantity(
                            timestamp=timestamp,
                            book_side=book_side,
                            new_best_quantity=best_quantity)
                        )
                    # if price was not at best, but there's no quantity outstanding
                    # we remove this price level
                    elif this_orderbook[price] == 0:
                        this_orderbook.pop(price)

            # Order Replace Message
            elif message_type == b"U":
                message = self.unpack(">iqqii", message)
                timestamp = self.microseconds + int(message[0] * 1e-3)
                # old order
                old_order_no = message[1]
                old_order = self.orders[old_order_no]
                old_order_price = old_order["price"]
                old_quantity_outstanding = old_order["quantity_outstanding"]
                book_side = old_order["book_side"]
                orderbook_no = old_order["orderbook_no"]
                self.orders.pop(old_order_no)
                # new order
                new_order_no = message[2]
                quantity = message[3]
                price = message[4]
                # create new order entry
                new_order = self.orders[new_order_no]
                new_order["book_side"] = book_side
                new_order["quantity_outstanding"] = quantity
                new_order["orderbook_no"] = orderbook_no
                new_order["price"] = price

                # adjust orderbook
                if orderbook_no in self.blue_chip_orderbooks:
                    this_orderbook = self.orderbooks[orderbook_no][book_side]
                    # old order
                    this_orderbook[old_order_price] -= old_quantity_outstanding
                    best_price, best_quantity = this_orderbook.peekitem(0)
                    if old_order_price == best_price:
                        if best_quantity == 0:
                            # if there is no quantity left at that price, we remove
                            # this price level and note that there's a new best price
                            this_orderbook.pop(old_order_price)
                            best_price, best_quantity = this_orderbook.peekitem(0)
                            self.best_bid_ask[orderbook_no].append(self.NewBestPrice(
                                timestamp=timestamp,
                                book_side=book_side,
                                new_best_price=best_price)
                            )
                        # in any case, if the price was at best, we note the new best quantity
                        self.best_depths[orderbook_no].append(self.NewBestQuantity(
                            timestamp=timestamp,
                            book_side=book_side,
                            new_best_quantity=best_quantity)
                        )
                    # if price was not at best, but there's no quantity outstanding
                    # we remove this price level
                    elif this_orderbook[old_order_price] == 0:
                        this_orderbook.pop(old_order_price)

                    # new order
                    this_orderbook[price] += quantity
                    # record if price was at best
                    best_price, best_quantity = this_orderbook.peekitem(0)
                    if price == best_price:
                        self.best_depths[orderbook_no].append(self.NewBestQuantity(
                            timestamp=timestamp,
                            book_side=book_side,
                            new_best_quantity=best_quantity)
                        )
                        # if it's the only one at the best price
                        if quantity == best_quantity:
                            self.best_bid_ask[orderbook_no].append(self.NewBestPrice(
                                timestamp=timestamp,
                                book_side=book_side,
                                new_best_price=price)
                            )

            # Order Executed Message
            elif message_type == b"E":
                message = self.unpack(">iqiq", message)
                timestamp = self.microseconds + int(message[0] * 1e-3)
                order_no = message[1]
                executed_quantity = message[2]
                match_number = message[3]
                # update the order entry
                this_order = self.orders[order_no]
                this_order["quantity_outstanding"] -= executed_quantity
                orderbook_no, book_side, price, quantity_outstanding = self.get_order_info(
                    this_order
                )
                if quantity_outstanding == 0:
                    self.orders.pop(order_no)
                # order book
                if orderbook_no in self.blue_chip_orderbooks:
                    this_orderbook = self.orderbooks[orderbook_no]
                    # info to calculate effective spreads
                    best_bid_price, best_bid_quantity = this_orderbook[b"B"].peekitem(0)
                    best_ask_price, best_ask_quantity = this_orderbook[b"S"].peekitem(0)
                    self.transactions[orderbook_no].append(self.Transaction(
                        timestamp=timestamp,
                        price=price,
                        size=executed_quantity,
                        best_bid=best_bid_price,
                        best_ask=best_ask_price,
                        best_ask_quantity=best_ask_quantity,
                        best_bid_quantity=best_bid_quantity,
                    ))
                    # update the order book
                    this_orderbook = this_orderbook[book_side]
                    this_orderbook[price] -= executed_quantity
                    best_price, best_quantity = this_orderbook.peekitem(0)
                    if price == best_price:
                        if best_quantity == 0:
                            # if there is no quantity left at that price, we remove
                            # this price level and note that there's a new best price
                            this_orderbook.pop(price)
                            best_price, best_quantity = this_orderbook.peekitem(0)
                            self.best_bid_ask[orderbook_no].append(self.NewBestPrice(
                                timestamp=timestamp,
                                book_side=book_side,
                                new_best_price=best_price)
                            )
                        # in any case, if the price was at best, we note the new best quantity
                        self.best_depths[orderbook_no].append(self.NewBestQuantity(
                            timestamp=timestamp,
                            book_side=book_side,
                            new_best_quantity=best_quantity)
                        )
                    # if price was not at best, but there's no quantity outstanding
                    # we remove this price level
                    elif this_orderbook[price] == 0:
                        this_orderbook.pop(price)


            # Order Executed With Price message
            elif message_type == b"C":
                message = self.unpack(">iqiqsi", message)
                # timestamp = self.microseconds + message[0] * 1e-3
                order_no = message[1]
                executed_quantity = message[2]
                # match_number = message[3]
                # printable = message[4]
                # execution_price = message[5]
                # update the order entry
                this_order = self.orders[order_no]
                orderbook_no, book_side, price, _ = self.get_order_info(this_order)
                this_order["quantity_outstanding"] -= executed_quantity
                # update the order
                if this_order["quantity_outstanding"] == 0:
                    self.orders.pop(order_no)
                # update the order book
                if orderbook_no in self.blue_chip_orderbooks:
                    this_orderbook = self.orderbooks[orderbook_no][book_side]
                    this_orderbook[price] -= executed_quantity
                    if this_orderbook[price] == 0:
                        this_orderbook.pop(price)


            # Orderbook Directory message
            elif message_type == b"R":
                message = self.unpack(">iis12s3s8siiiiii", message)

                # initialize each side of the orderbook
                orderbook_no = message[1]
                this_orderbook = self.orderbooks[orderbook_no]
                this_orderbook[b"B"] = OrderBookSide(neg)
                this_orderbook[b"S"] = OrderBookSide()
                this_orderbook[b" "] = OrderBookSide()

                group = message[5]
                this_metadata = self.metadata[orderbook_no]
                this_metadata["group"] = group
                if group == b"ACoK    ":
                    self.blue_chip_orderbooks.append(orderbook_no)
                    this_metadata["price_type"] = message[2]
                    this_metadata["isin"] = message[3]
                    this_metadata["currency"] = message[4]
                    this_metadata["minimum_quantity"] = message[6]
                    this_metadata["quantity_tick_table_id"] = message[7]
                    this_metadata["price_tick_table_id"] = message[8]
                    this_metadata["price_decimals"] = message[9]
                    this_metadata["delisting_date"] = message[10]
                    this_metadata["delisting_time"] = message[11]

            # Price Tick Size message
            elif message_type == b"L":
                message = self.unpack(">iiii", message)
                # timestamp = self.microseconds + message[0] * 1e-3
                price_tick_table_id = message[1]
                this_tick_size_table = self.price_tick_sizes[price_tick_table_id]
                # price_tick_size = message[2]
                # price_start = message[3]
                this_tick_size_table[message[2]] = message[3]

            # Quantity Tick Size message
            elif message_type == b"M":
                message = self.unpack(">iiii", message)
                # timestamp = self.microseconds + message[0] * 1e-3
                quantity_tick_table_id = message[1]
                quantity_tick_size = message[2]
                quantity_start = message[3]

            # Orderbook Trading Action message
            elif message_type == b"H":
                message = self.unpack(">iiss", message)
                timestamp = self.microseconds + int(message[0] * 1e-3)
                orderbook_no = message[1]
                trading_state = message[2]
                book_condition = message[3]
                self.trading_actions[orderbook_no].append((timestamp, trading_state, book_condition))

            else:
                pass  # because message type is not relevant

            # update current position for next iteration
            self.current_position = message_end

            # # System Event message
            # elif message_type == b"S":
            #     message = self.unpack(">i8ssi", message)
            #     timestamp = self.microseconds + message[0] * 1e-3
            #     group = message[1]
            #     event_code = message[2]
            #     orderbook_no = message[3]

            # # Indicative Price / Quantity Message
            # elif message_type == b"I":
            #     # message = self.unpack(">iqiiiis", message)
            #     pass # not relevant

            # # Trade message (SwissAtMid / EBBO)
            # elif message_type == b"P":
            #     message = self.unpack(">iiiiqs", message)
            #     timestamp = self.microseconds + message[0] * 1e-3
            #     orderbook_no = message[1]
            #     executed_quantity = message[2]
            #     execution_price = message[3]
            #     match_number = message[4]
            #     book_type = message[5]

            # # Broken Trade message
            # elif message_type == b"B":
            #     message = self.unpack(">iqs", message)
            #     timestamp = self.microseconds + message[0] * 1e-3
            #     match_number = message[1]
            #     reason = message[2]

            # # Orderbook Trading Action message
            # elif message_type == b"H":
            #     message = self.unpack(">iiss", message)
            #     timestamp = self.microseconds + message[0] * 1e-3
            #     orderbook_no = message[1]
            #     trading_state = message[2]
            #     book_condition = message[3]

            # elif message_type == b"G":  # not relevant
            #     pass

            # else:
            #     raise ValueError(f"Message type {message_type} could not be found")

            # # update current position for next iteration
            # self.current_position = message_end
