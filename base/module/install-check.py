import sys

try:
    from gi.repository import GObject
    if sys.platform.startswith("linux"):
        import xdg.BaseDirectory
        import dbus
    import PIL
    import logging
# disable temporary do not commit
#    import bluetooth
except ImportError as e:
    print("")
    print("+-----------------------------------------------------------------+")
    print("| Unsatisfied Python requirement: %s." % e)
    print("| Please install the missing module and then retry.")
    print("+-----------------------------------------------------------------+")
    print("")
    sys.exit(1)
