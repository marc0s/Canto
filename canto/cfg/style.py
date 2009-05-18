from canto.interface_draw import Renderer
import curses

INVALID_COLOR = -2

colordir = {"default" : -1,
    "black" : 0,
    "white" : 7,
    "red" : 1,
    "green" : 2,
    "yellow" : 3,
    "blue" : 4,
    "magenta" : 5,
    "pink" : 5,
    "cyan" : 6}


def convcolor(c):
    if type(c) == int:
        if 0 <= c <= curses.COLORS:
            return c
        else:
            return INVALID_COLOR
    elif type(c) == str:
        if c in colordir:
            return colordir[c]
    return INVALID_COLOR

def register(c):
    c.colors = [("white","black"),("blue","black"),("yellow","black"),\
        ("green","black"),("pink","black"),("black","black"),\
        ("blue","black"),(0,0)]

    c.default_renderer = Renderer()
    c.default_msg_tick = 5
    
    def set_default_renderer(renderer):
        c.default_renderer = renderer

    def get_default_renderer():
        return c.default_renderer

    c.locals.update({
        "colors" : c.colors,
        "renderer" : Renderer,
        "default_renderer" : set_default_renderer,
        "get_default_renderer" : get_default_renderer})

def post_parse(c):
    c.colors = c.locals["colors"]

# Acceptable colors are either one off strings (like "blue") or ints (4) that
# get converted into a tuple with the same background as the first color pair
# (or "default" if it is the first color pair) or they're full tuples (fg, bg).

# Int colors have to be 0 <= x < curses.COLORS
# String colors have to be in the colordir (above)

# The color array must also be 8 entries long.

def validate_colors(colors, len_check = 1):
    newcolors = []

    if len_check and len(colors) != 8:
        raise Exception, "colors array must have 8 entries"

    for i, color in enumerate(colors):
        if type(color) in [int, str, unicode]:
            color = tuple([color])
        if type(color) == tuple:
            if len(color) > 2:
                raise Exception, "%s is not a valid color pair (too long)"\
                        % color
            elif len(color) == 1:
                realcolor = convcolor(color[0])
                if realcolor == INVALID_COLOR:
                    raise Exception, "%s is not a valid color" % color[0]
                else:
                    if i == 0:
                        color = (color[0], "default")
                    else:
                        color = (color[0], newcolors[0][1])

            fg = convcolor(color[0])
            if fg == INVALID_COLOR:
                raise Exception, "%s is not a valid foreground color" % color[0]

            bg = convcolor(color[1])
            if bg == INVALID_COLOR:
                raise Exception, "%s is not a valid background color" % color[1]

            newcolors.append((fg, bg))
        else:
            raise Exception, "Unknown type for color: %s" % type(color)

    return newcolors

def validate_renderer(r):
    if not isinstance(r, BaseRenderer):
        raise Exception,\
            "Renderers must be subclass of BaseRenderer in canto.interface_draw"

def validate(c):
    # No need to validate colors is curses isn't init'd
    # (i.e. the config is being parsed in canto-fetch)

    if hasattr(curses, "COLORS"):
        c.colors = validate_colors(c.colors)

def test(c):
    # We have to initscr to get curses.COLORS to be correct
    curses.initscr()
    curses.start_color()

    try:
        # One off int color (valid)
        t = validate_colors([1], 0)
        if t != [(1,-1)]:
            raise Exception, "Failed to convert int to color pair (%s)" % t

        # One off int color (invalid)
        try:
            i = colors.COLORS + 1
            t = validate_colors([i], 0)
        except:
            pass
        else:
            raise Exception, "Invalid color (%d) didn't raise exception" % i

        for c in colordir.keys():
            # One off string color (valid)
            t = validate_colors([c], 0)
            if t != [(colordir[c], -1)]:
                raise Exception, \
                        "Failed to convert %s to color pair (%s)" % (c,t)

            # String tuple (valid)
            input = (c, "default")
            t = validate_colors([input], 0)
            if t[0] == [(colordir[c], colordir["default"])]:
                raise Exception,\
                        "Failed to convert %s to color pair (%s)" % (input,t)

        #One off string color (invalid)
        try:
            s = "bullshit"
            t = validate_colors([s], 0)
        except:
            pass
        else:
            raise Exception, "Invalid color (%s) didn't raise exception" % s

        #String tuple (invalid)
        for t in [("bullshit", "default"), ("default", "bullshit")]:
            try:
                validate_colors(t, 0)
            except:
                pass
            else:
                raise Exception,\
                        "Invalid string tuple (%s) didn't raise exception" % t

        #Numeric tuple (valid)
        for fg in xrange(curses.COLORS):
            for bg in xrange(curses.COLORS):
                t = validate_colors([(fg, bg)], 0)
                if t != [(fg, bg)]:
                    raise Exception,\
                        "Valid color pair %s failed" % (fg, bg)

        #Numeric tuple (invalid)
        for fg, bg in [(curses.COLORS + 1, 0), (0, curses.COLORS + 1)]:
            try:
                t = validate_colors([(fg, bg)], 0)
            except:
                pass
            else:
                raise Exception, "Invalid color pair didn't raise exception"

        #Default bg
        i = [(1, 2), 1]
        t = validate_colors(i, 0)
        if t != [(1,2), (1, 2)]:
            raise Exception, "Default background failed"

        #Len check (valid)
        i = [(0,0)] * 8
        t = validate_colors(i)
        if t != i:
            raise Exception, "Valid len check failed"

        #Len check (invalid)
        i = []
        try:
            t = validate_colors(i)
        except:
            pass
        else:
            raise Exception, "Invalid len check failed"

    except:
        raise
    finally:
        curses.endwin()

    print "Style tests passed."
