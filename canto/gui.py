# -*- coding: utf-8 -*-

#Canto - ncurses RSS reader
#   Copyright (C) 2008 Jack Miller <jack@codezen.org>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License version 2 as 
#   published by the Free Software Foundation.

# Where thread.py is the work-horse of the worker thread (hah, aptly named),
# gui.py is where most of the important work for the interface thread is done.

# The Gui class does a number of things. First of all, it handles the main list
# window. It creates it, writes to it, and destroys it. Second of all, it
# handles all of the keybinds.

# The core data structure of Gui is the map. The map includes one entry for all
# items that are visible or could possibly be visible given the current settings
# like global filters, tag filters, and current tags. Each entry in the map
# is a dict that looks like this:

# d["item"]     -> the Story() object
# d["tag"]      -> the Tag() object the story is in.
# d["row"]      -> the row the item is on
# d["lines"]    -> the number of lines the item takes up, given the current 
#                   width the screen.

# This information is regenerated each time the items change or the screen size
# changes. All of these keybinds end up traversing the map somehow, and
# particularly the self.sel variable that indicates what is selected.

# The Gui class has a number of important functions.
# refresh()         -> create and resize the main window.
# __map_items()     -> regenerate the map
# draw_elements()   -> actually draw to the screen
# key()             -> converts a single key to a group of actions
# action()          -> perform a list of actions
# alarm()           -> takes the diffs generated by the worker thread and
#                       integrates it into the current tags

# Most of these significant functions relay their events to a Reader() object,
# if necessary.

# The rest of the functions are keybinds or helpers for said keybinds.

from cfg.filters import validate_filter
from cfg.sorts import validate_sort

from input import input, search, num_input
from basegui import BaseGui
from reader import Reader
from const import *
import utility
import extra 

import curses

# DECORATOR DEFINITIONS
# The following are a number of decorators that move a lot of repeating code out
# of the gui itself. These provide some level of consistency between similar
# functions without their true intents being lost in what amounts to template
# code.

# noitem_unsafe makes a keybind fail with a message if it requires items to be
# present. Basically anything that doesn't change global settings is going to
# make this check.

def noitem_unsafe(fn):
    def ns_dec(self, *args):
        if self.items:
            return fn(self, *args)
        else:
            self.cfg.log("No Items.")
    return ns_dec

# change_selected handles the select/unselect hooks, and the change_tag trigger.

def change_selected(fn):
    def dec(self, *args):
        # Don't unselect before the function so that the function can still
        # ascertain what item is selected.
        oldsel = self.sel

        r = fn(self, *args)

        if oldsel:
            oldsel["item"].unselect()
            if self.cfg.unselect_hook:
                self.cfg.unselect_hook(oldsel["tag"], oldsel["item"])

        if self.sel:
            self.sel["item"].select()
            if self.cfg.select_hook:
                self.cfg.select_hook(self.sel["tag"], self.sel["item"])

        if "change_tag" in self.cfg.triggers and\
            oldsel and self.sel and \
            oldsel["tag"] != self.sel["tag"]:
                self.change_tag_override = 1
        return r
    return dec

# The rest of the decorators are self-explanatory, mostly just printing out a
# log message when their decorated function is called.

def change_filter(fn):
    def dec(self, *args):
        r,f = fn(self, *args)
        if r:
            self.cfg.log("Filter: %s" % f)
            for t in self.tags:
                t.clear()
            return REFILTER
    return dec

def change_tag_filter(fn):
    def dec(self, *args):
        r,f = fn(self, *args)
        if r:
            self.cfg.log("Tag Filter: %s" % f)
            return TFILTER
    return dec

def change_sorts(fn):
    def dec(self, *args):
        r,s = fn(self, *args)
        if r:
            self.cfg.log("Sort: %s" % s)

            # Because sorts, unlike filters, don't change the items that are
            # present in the tag, we can do these ASAP, whereas the filters have
            # to be moved through the work thread.

            self.sel["tag"].sort(s)
            self.sel["tag"].enum()
            return UPDATE
    return dec

def change_tags(fn):
    def dec(self, *args):
        r,t = fn(self, *args)
        if r:
            for ot in self.tags:
                ot.clear()
            self.tags = t
            self.sel = None
            self.cfg.log("Tags: %s" % ", ".join([unicode(x) for x in t]))
            return RETAG
    return dec

# The main class.

class Gui(BaseGui) :
    def __init__(self, cfg, tags):
        self.keys = cfg.key_list
        self.window_list = []
        self.map = []
        self.reader_obj = None

        self.cfg = cfg

        self.lines = 0
        self.sel = None

        self.offset = 0
        self.max_offset = 0

        self.tags = tags
        self.change_tag_override = 0

        if self.cfg.start_hook:
            self.cfg.start_hook(self)

    def refresh(self):
        # Generate all of the columns
        self.window_list = [curses.newpad(self.cfg.gui_height + 1, \
                    self.cfg.gui_width / self.cfg.columns)\
                    for i in range(0, self.cfg.columns)]

        # Setup the backgrounds.
        for window in self.window_list:
            window.bkgdset(curses.color_pair(1))

        # Self.lines is the maximum number of visible lines on the screen
        # at any given time. Used for scroll detection.

        self.lines = self.cfg.columns * self.cfg.gui_height

        # A redraw indicates that the map must be regenerated.
        self.__map_items()

        # Pass to the reader
        if self.reader_obj:
            self.reader_obj.refresh()
        self.draw_elements()

    def __map_items(self):

        # This for loop populates self.map with all stories that
        #   A - are first in a collapsed feed or not in one at all.
        #   B - that actually manage to print something to the screen.
        
        # We keep track of the "virtual row"

        # Essentially, map pretends that we're drawing onto an infinitely long
        # single window, and it's up to draw_elements to determine what range in
        # the map is actually visible and then it's up to the Renderer()
        # (particularly Renderer.__window()) to convert that into a real window
        # and row to draw to.

        self.map = []
        self.items = 0
        row = 0
        for i, tag in enumerate(self.tags):
            for item in tag:
                if not tag.collapsed or item.idx == 0:
                    lines = self.print_item(tag, item, 0)
                    if lines:
                        self.map.append(
                            {"tag" : tag,
                             "row" : row,
                             "item" : item,
                             "lines" : lines})
                        self.items += 1

                        if self.items == 1:
                            self.map[0]["prev"] = 0
                        else:
                            self.map[-2]["next"] = self.items - 1
                            self.map[-1]["prev"] = self.items - 2
                        row += lines

        if self.items:
            self.map[-1]["next"] = self.items - 1

        # Set max_offset, this is how we know not to recenter the
        # screen when it would leave unused space at the end.
        self.max_offset = row - self.lines

    # Print a single item to the screen.
    def print_item(self, tag, story, row):
        d = { "story" : story, "tag" : tag, "row" : row, "cfg" : self.cfg,
                "width" : self.cfg.width / self.cfg.columns,
                "window_list" : self.window_list }
        r = tag.renderer.story(d)

        # Dereference anything that was fetched from disk
        story.free()

        return r

    # Print all stories in self.map. Ignores all off screen items.
    def draw_elements(self):
        if self.items:
            self.__check_scroll()
            row = -1 * self.offset
            for item in self.map:
                # If row is not offscreen up
                if item["row"] + item["lines"] > self.offset:

                    # If row is offscreen down
                    if item["row"] > self.lines + self.offset:
                        break

                    self.print_item(item["tag"], item["item"], row)
                row += item["lines"]
        else:
            row = -1

        # Actually perform curses screen update.
        for i,win in enumerate(self.window_list) :
            # Clear unused space (entirely or partially empty columns)
            if i * self.cfg.gui_height >= row:
                win.erase()
            else:
                win.clrtobot()

            win.noutrefresh(0,0,
                    self.cfg.gui_top,
                    i*(self.cfg.gui_width / self.cfg.columns),
                    self.cfg.gui_height - 1,
                    (i+1)*(self.cfg.gui_width / self.cfg.columns))

        if self.reader_obj:
            self.reader_obj.draw_elements()
        curses.doupdate()

    # This is only overridden to pass to the reader, otherwise the BaseGui key
    # implementation would be suitable.

    def key(self, k):
        if self.reader_obj:
            return self.reader_obj.key(k)
        return BaseGui.key(self, k)

    def action(self, a):
        if self.reader_obj:
            return self.reader_obj.action(a)
        r = BaseGui.action(self, a)

        # The change_tag_override forces the return to be UPDATE
        if self.change_tag_override:
            self.change_tag_override = 0
            return UPDATE

        return r

    def __check_scroll(self) :
        # If our current item is offscreen up, ret 1
        if self.sel["row"] < self.offset :
            self.offset = self.sel["row"]
            return 1

        # If our current item is offscreen down, ret 1
        if self.sel["row"] + self.sel["lines"] > self.lines + self.offset :
            self.offset = self.sel["row"] + self.sel["lines"] - self.lines
            return 1
        return 0

    @change_selected
    def alarm(self, new=[], old=[]):

        # This is where the item diffs generated by the worker thread are
        # integrated into the currently displayed tags. 

        for lst in [new, old]:
            if lst:
                for i, t in enumerate(lst):
                    if not t:
                        continue

                    # global filter, tag filter, tag sort, added/removed items
                    gf, tf, s, l = t

                    # Check that the diff was created with the same filters and
                    # sorts that are still in play

                    gf = self.cfg.all_filters[gf]
                    tf = self.cfg.all_filters[tf]
                    s = self.cfg.all_sorts[s]

                    if l and self.tags[i].sorts.cur() == s and\
                        self.tags[i].filters.cur() == tf and\
                        self.cfg.filters.cur() == gf:
                        # Add or remove them as necessary
                        if lst == old:
                            self.tags[i].retract(l)
                        else:
                            self.tags[i].extend(l)

        # Remap since we may have added or removed items
        self.__map_items() 

        # At this point, the items have successfully been integrated into the
        # running tags, so now we just attempt to maintain our current selection
        # status.

        if self.items:
            if self.sel:
                for item in self.map:
                    if item["item"] == self.sel["item"] and\
                       item["tag"] == self.sel["tag"]:
                        # Item is in map, no problem.
                        self.sel = item
                        break
                else:
                    # Not in the map, try to move to the top of the tag
                    self.__select_topoftag()
            else:
                # No selection made, select the first item possible. This is the
                # initial case and the case after a tag change or refilter.
                self.__select_topoftag(0)

        # If we had a selection, and now no items
        elif self.sel:
            self.cfg.log("No Items.")
            self.sel = None

        if self.cfg.update_hook:
            self.cfg.update_hook(self)

    @noitem_unsafe
    @change_selected
    def __select_topoftag(self, t=-1):
        if t < 0:
            # Default case, attempt to select the top of the current tag.
            ts = self.tags[self.tags.index(self.sel["tag"]):]
        else:
            ts = self.tags[t:]

        for i in xrange(len(self.map)):
            if self.map[i]["tag"] in ts:
                self.sel = self.map[i]
                break
        else:
            self.sel = self.map[0]

    @noitem_unsafe
    @change_selected
    def next_item(self):
        self.sel = self.map[self.sel["next"]]

    @noitem_unsafe
    def next_tag(self):
        curtag = self.sel["tag"]
        while self.sel != self.map[-1]:
            if curtag != self.sel["tag"]:
                break
            self.next_item()

        # Next_tag should try to keep the top of the tag at
        # the top of the screen (as prev_tag does inherently)
        # so that the user's eye isn't lost.
        self.offset = min(self.sel["row"], max(0, self.max_offset))

    @noitem_unsafe
    @change_selected
    def prev_item(self):
        self.sel = self.map[self.sel["prev"]]

    @noitem_unsafe
    def prev_tag(self):
        curtag = self.sel["tag"]
        while self.sel != self.map[0]:
            if curtag != self.sel["tag"] and \
                    self.sel["item"] == self.sel["tag"][0]:
                break
            self.prev_item()

    # Goto_tag goes to an absolute #'d tag. So the third
    # tag defined in your configuration will always be '3'

    @noitem_unsafe
    @change_selected
    def goto_tag(self, num = None):
        if not num:
            num = num_input(self.cfg, "Absolute Tag")
        if num == None:
            return

        # Simple wrapping like python, so -1 is the last, -2 is the second to
        # last, etc. etc.

        if num < 0:
            num = len(self.tags) + num
        num = min(len(self.tags) - 1, num)
 
        target = self.tags[num]
        for item in self.map:
            if item["tag"] == target:
                self.sel = item
                break
        else:
            self.cfg.log("Abolute Tag %d not visible" % num)

    # Goto_reltag goes to a tag relative to what's visible.

    @noitem_unsafe
    @change_selected
    def goto_reltag(self, num = None):
        if not num:
            num = num_input(self.cfg, "Tag")
        if not num:
            return

        idx = self.tags.index(self.sel["tag"])
        idx = max(min(len(self.tags) - 1, idx + num), 0)
        target = self.tags[idx]
        for item in self.map:
            if item["tag"] == target:
                self.sel = item
                break

    @noitem_unsafe
    @change_selected
    def next_filtered(self, f) :
        cursor = self.map[self.sel["next"]]
        while True:
            if f(cursor["tag"], cursor["item"]):
                self.sel = cursor
                return
            if cursor == self.map[-1]:
                return
            cursor = self.map[cursor["next"]]

    @noitem_unsafe
    @change_selected
    def prev_filtered(self, f) :
        cursor = self.map[self.sel["prev"]]
        while True:
            if f(cursor["tag"], cursor["item"]):
                self.sel = cursor
                return
            if cursor == self.map[0]:
                return
            cursor = self.map[cursor["prev"]]

    def next_mark(self):
        self.next_filtered(extra.show_marked())

    def prev_mark(self):
        self.prev_filtered(extra.show_marked())

    def next_unread(self):
        self.next_filtered(extra.show_unread())

    def prev_unread(self):
        self.prev_filtered(extra.show_unread())

    @noitem_unsafe
    def just_read(self):
        self.sel["tag"].set_read(self.sel["item"])

    @noitem_unsafe
    def just_unread(self):
        self.sel["tag"].set_unread(self.sel["item"])

    @noitem_unsafe
    def goto(self) :        
        self.sel["tag"].set_read(self.sel["item"])
        self.draw_elements()
        utility.goto(("", self.sel["item"]["link"], "link"), self.cfg)

    def help(self):
        self.cfg.wait_for_pid = utility.silentfork("man canto", "", 1, 0)

    @noitem_unsafe
    def reader(self) :
        self.reader_obj = Reader(self.cfg, self.sel["tag"],\
                self.sel["item"], self.reader_dead)
        return REDRAW_ALL

    # This is the callback when the reader is done.
    def reader_dead(self):
        self.reader_obj = None

    @change_filter
    def set_filter(self, filt):
        filt = validate_filter(self.cfg, filt)
        return (self.cfg.filters.override(filt), self.cfg.filters.cur())

    @change_filter
    def next_filter(self):
        return (self.cfg.filters.next(), self.cfg.filters.cur())

    @change_filter
    def prev_filter(self):
        return (self.cfg.filters.prev(), self.cfg.filters.cur())

    @noitem_unsafe
    @change_tag_filter
    def set_tag_filter(self, filt):
        return (self.sel["tag"].filters.override(filt),\
                self.sel["tag"].filters.cur())

    @noitem_unsafe
    @change_tag_filter
    def next_tag_filter(self):
        self.cfg.log("%s" % self.sel["tag"].filters)
        return (self.sel["tag"].filters.next(),\
                self.sel["tag"].filters.cur())

    @noitem_unsafe
    @change_tag_filter
    def prev_tag_filter(self):
        return (self.sel["tag"].filters.prev(),\
                self.sel["tag"].filters.cur())

    @noitem_unsafe
    @change_sorts
    def next_tag_sort(self):
        return (self.sel["tag"].sorts.next(),
                self.sel["tag"].sorts.cur())

    @noitem_unsafe
    @change_sorts
    def prev_tag_sort(self):
        return (self.sel["tag"].sorts.prev(),
                self.sel["tag"].sorts.cur())

    @noitem_unsafe
    @change_sorts
    def set_tag_sort(self, sort):
        sort = validate_sort(self.cfg, sort)
        return (self.sel["tag"].sorts.override(sort),\
                self.sel["tag"].sorts.cur())

    @change_tags
    def next_tagset(self):
        return (self.cfg.tags.next(), self.cfg.tags.cur())

    @change_tags
    def prev_tagset(self):
        return (self.cfg.tags.prev(), self.cfg.tags.cur())

    @change_tags
    def set_tagset(self, t):
        return (1, self.cfg.get_real_tagl(t))

    @noitem_unsafe
    def inline_search(self):
        self.do_inline_search(search(self.cfg, "Inline Search"))

    def do_inline_search(self, s) :
        if s:
            for t in self.tags:
                for story in t:
                    if s.match(story["title"]):
                        story.set("marked")
                    else:
                        story.unset("marked")

            self.prev_mark()
            self.next_mark()
            self.draw_elements()

    @noitem_unsafe
    def toggle_mark(self):
        if self.sel["item"].was("marked"):
            self.sel["item"].unset("marked")
        else:
            self.sel["item"].set("marked")

    @noitem_unsafe
    def all_unmarked(self):
        for item in self.map:
            if item["item"].was("marked"):
                item["item"].unset("marked")

    @noitem_unsafe
    def toggle_collapse_tag(self):
        self.sel["tag"].collapsed =\
                not self.sel["tag"].collapsed
        self.sel["item"].unselect()
        self.__map_items()
        self.__select_topoftag()

    def __collapse_all(self, c):
        for t in self.tags:
            t.collapsed = c
        self.__map_items()
        self.__select_topoftag()

    # These are convenience functions so that keybinds don't have to be lambdas
    # or other functions and can therefore be more easily manipulated.

    def set_collapse_all(self):
        self.__collapse_all(1)

    def unset_collapse_all(self):
        self.__collapse_all(0)

    def force_update(self):
        self.cfg.log("Forcing update.")
        for f in self.cfg.feeds :
            f.time = 1
        return UPDATE
    
    @noitem_unsafe
    def tag_read(self):
        self.sel["tag"].all_read()

    def all_read(self):
        for t in self.tags:
            t.all_read()

    @noitem_unsafe
    def tag_unread(self):
        self.sel["tag"].all_unread()

    def all_unread(self):
        for t in self.tags :
            t.all_unread()

    def quit(self):
        if self.cfg.end_hook:
            self.cfg.end_hook(self)
        return EXIT
