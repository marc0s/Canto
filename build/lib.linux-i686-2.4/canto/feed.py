# -*- coding: utf-8 -*-

#Canto - ncurses RSS reader
#   Copyright (C) 2007 Jack Miller <jjm2n4@umr.edu>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License version 2 as 
#   published by the Free Software Foundation.

import sys
import story
import utility
import interface_draw
import re
import codecs
import tag

class Feed(tag.Tag):
    """Feed() encapsulates a feed directory and handles
    all updates in that feed directory when ticked()"""

    def __init__(self, cfg, dirpath, handle, URL, rate, keep):
        tag.Tag.__init__(self)
        self.path = dirpath
        self.handle = handle
        self.URL = URL
        self.cfg = cfg

        if self.path : self.update()

        self.rate = rate
        self.time = 1
        self.keep = keep
        
    def update(self):
        """Invoke an update, reading all of the stories
        from the disk."""

        newlist = []
        try:
            fsock = codecs.open(self.path + "/idx", "r", "UTF-8", "ignore")
            try:
                data = fsock.read().split("\00")[:-1]
            finally:
                fsock.close()

            for item in data:
                path = self.path + "/" + item.replace("/", " ")
                newlist.append(story.Story(path))

        except IOError:
            pass
        
        for i in range(len(newlist)):
            r = self.search_stories(newlist[i])
            if r != -1 :
                newlist[i] = self[r]
        
        for i in range(len(self)):
            self.pop()
        self.extend(newlist)

    def tick(self):
        self.time -= 1
        if self.time <= 0 : 
            self.update()
            if len(self) == 0 :
                self.time = 1
            else :
                self.time = self.rate
