# =============================================================================
#
#    Remuco - A remote control system for media players.
#    Copyright (C) 2006-2010 by the Remuco team, see AUTHORS.
#
#    This file is part of Remuco.
#
#    Remuco is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    Remuco is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with Remuco.  If not, see <http://www.gnu.org/licenses/>.
#
# =============================================================================

import inspect
import math # for ceiling
import os
import os.path
import subprocess
import urllib
from urllib import parse

from gi.repository import GConf, GObject

from remuco import art
from remuco import config
from remuco import files
from remuco import log
from remuco import message
from remuco import net
from remuco import serial

from remuco.defs import *
from remuco.features import *

from remuco.data import PlayerInfo, PlayerState, Progress, ItemList, Item
from remuco.data import Control, Action, Tagging, Request

from remuco.manager import NoManager

# =============================================================================
# reply class for requests
# =============================================================================

class ListReply(object):
    """Reply object for an item list request.
    
    A ListReply is the first parameter of the request methods
    PlayerAdapter.request_playlist(), PlayerAdapter.request_queue(),
    PlayerAdapter.request_mlib() and PlayerAdapter.request_search().
    
    Player adapters are supposed to use the list reply object to set the
    reply data (using properties 'ids', 'names', 'item_actions' and
    'nested', 'list_actions') and to send the reply to clients (using send()).
    
    """
    def __init__(self, client, request_id, reply_msg_id, page, path=None):
        """Create a new list reply.
        
        Used internally, not needed within player adapters.
        
        @param client: the client to send the reply to
        @param request_id: the request's ID
        @param reply_msg_id: the message ID of the client's request
        @param page: page of the requested list
        
        @keyword path: path of the requested list, if there is one
        
        """
        self.__client = client
        self.__request_id = request_id
        self.__reply_msg_id = reply_msg_id
        self.__page = page
        self.__path = path
        
        self.__nested = []
        self.__ids = []
        self.__names = []
        self.__list_actions = []
        self.__item_actions = []
        
    def send(self):
        """Send the requested item list to the requesting client."""
        
        ### paging ###
        
        page_size = self.__client.info.page_size
        len_all = len(self.__ids or []) + len(self.__nested or [])
        # P3K: remove float() and int()
        page_max = int(max(math.ceil(float(len_all) / page_size) - 1, 0))
        
        # number of pages may have changed since client sent the request
        self.__page = min(self.__page, page_max)
        
        index_start = self.__page * page_size
        index_end = index_start + page_size
        
        nested, ids, names = [], [], []
        item_offset = 0
        
        if self.__nested and index_start < len(self.__nested):
            # page contains nested lists and maybe items
            nested = self.__nested[index_start:index_end]
            if len(nested) < page_size:
                # page contains nested lists and items
                num_items = page_size - len(nested)
                ids = self.__ids[0:num_items]
                names = self.__names[0:num_items]
        else:
            # page contains only items
            index_start -= len(self.__nested)
            index_end -= len(self.__nested)
            ids = self.__ids[index_start:index_end]
            names = self.__names[index_start:index_end]
            item_offset = index_start
        
        
        ### sending ###
        
        ilist = ItemList(self.__request_id,
                         self.__path, nested, ids, names, item_offset,
                         self.__page, page_max,
                         self.__item_actions, self.__list_actions)
        
        msg = net.build_message(self.__reply_msg_id, ilist)
        
        GObject.idle_add(self.__client.send, msg)
        

    # === property: ids ===
    
    def __pget_ids(self):
        """IDs of the items contained in a list.
        
        Player adapters should set this to a list of IDs of the items contained
        in the requested list.
        
        """
        return self.__ids
    
    def __pset_ids(self, value):
        self.__ids = value
    
    ids = property(__pget_ids, __pset_ids, None, __pget_ids.__doc__)

    # === property: names ===
    
    def __pget_names(self):
        """Names of the items contained in a list.
        
        Player adapters should set this to a list of names of the items
        contained in the requested list. Good choice for a name is combination
        of artist and title.
        
        """
        return self.__names
    
    def __pset_names(self, value):
        self.__names = value
    
    names = property(__pget_names, __pset_names, None, __pget_names.__doc__)

    # === property: nested ===
    
    def __pget_nested(self):
        """Names of nested lists contained in a list.
        
        Player adapters should set this to a list of names of the nested lists
        contained in the requested list. To be used only for mlib requests (see
        PlayerAdapter.request_mlib()).
        
        """
        return self.__nested
    
    def __pset_nested(self, value):
        self.__nested = value
    
    nested = property(__pget_nested, __pset_nested, None, __pget_nested.__doc__)

    # === property: item_actions ===
    
    def __pget_item_actions(self):
        """A list of actions clients can apply to items in the list.
        
        The list must contain ItemAction objects.
        """
        return self.__item_actions
    
    def __pset_item_actions(self, value):
        self.__item_actions = value
    
    item_actions = property(__pget_item_actions, __pset_item_actions, None,
                            __pget_item_actions.__doc__)

    # === property: list_actions ===
    
    def __pget_list_actions(self):
        """A list of actions clients can apply to nested lists in the list.
        
        The list must contain ListAction objects.
        """
        return self.__list_actions
    
    def __pset_list_actions(self, value):
        self.__list_actions = value
    
    list_actions = property(__pget_list_actions, __pset_list_actions, None,
                            __pget_list_actions.__doc__)


# =============================================================================
# media browser actions
# =============================================================================

class ListAction(object):
    """List related action for a client's media browser.
    
    A list action defines an action a client may apply to a list from the
    player's media library. If possible, player adapters may define list
    actions and send them to clients via PlayerAdapter.replay_mlib_request()
    Clients may then use these actions which results in a call to
    PlayerAdapter.action_mlib_list().
    
    @see: PlayerAdapter.action_mlib_list()
     
    """
    __id_counter = 0
    
    def __init__(self, label):
        """Create a new action for lists from a player's media library.
        
        @param label:
            label of the action (keep short, ideally this is just a single word
            like 'Load', ..)
        
        """
        ListAction.__id_counter -= 1
        self.__id = ListAction.__id_counter
        
        self.label = label
        
    def __str__(self):
        
        return "(%d, %s)" % (self.__id, self.label)
        
    # === property: id ===
    
    def __pget_id(self):
        """ID of the action (auto-generated, read only)"""
        return self.__id
    
    id = property(__pget_id, None, None, __pget_id.__doc__)
    
class ItemAction(object):
    """Item related action for a client's media browser.
    
    An item action defines an action a client may apply to a file from the
    local file system, to an item from the playlist, to an item from the play
    queue or to an item from the player's media library.
      
    If possible, player adapters should define item actions and send them to
    clients by setting the keyword 'file_actions' in PlayerAdapter.__init__(),
    via PlayerAdapter.reply_playlist_request(), via
    PlayerAdapter.reply_queue_request() or via
    PlayerAdapter.reply_mlib_request(). Clients may then use these actions
    which results in a call to PlayerAdapter.action_files(),
    PlayerAdapter.action_playlist_item(), PlayerAdapter.action_queue_item() or
    PlayerAdapter.action_mlib_item().
    
    @see: PlayerAdapter.action_files()
    @see: PlayerAdapter.action_playlist()
    @see: PlayerAdapter.action_queue()
    @see: PlayerAdapter.action_mlib_item() 
    
    """
    __id_counter = 0
    
    def __init__(self, label, multiple=False):
        """Create a new action for items or files.
        
        @param label:
            label of the action (keep short, ideally this is just a single word
            like 'Enqueue', 'Play', ..)
        @keyword multiple:
            if the action may be applied to multiple items/files or only to a
            single item/file
        
        """
        ItemAction.__id_counter += 1
        self.__id = ItemAction.__id_counter
        
        self.label = label
        
        self.multiple = multiple
        
    def __str__(self):
        
        return "(%d, %s, %s)" % (self.id, self.label, self.multiple)
        
    # === property: id ===
    
    def __pget_id(self):
        """ID of the action (auto-generated, read only)"""
        return self.__id
    
    id = property(__pget_id, None, None, __pget_id.__doc__)
    
# =============================================================================
# player adapter
# =============================================================================

MIMETYPES_AUDIO = ("audio", "application/ogg")
MIMETYPES_VIDEO = ("video",)
MIMETYPES_AV = MIMETYPES_AUDIO + MIMETYPES_VIDEO

class PlayerAdapter(object):
    '''Base class for Remuco player adapters.
    
    Remuco player adapters must subclass this class and override certain
    methods to implement player specific behavior. Additionally PlayerAdapter
    provides methods to interact with Remuco clients. Following is a summary
    of all relevant methods, grouped by functionality. 
    
    ===========================================================================
    Methods to extend to manage life cycle
    ===========================================================================
    
        * start()
        * stop()
    
        A PlayerAdapter can be started and stopped with start() and stop().
        The same instance of a PlayerAdapter should be startable and stoppable
        multiple times.
        
        Subclasses of PlayerAdapter may override these methods as needed but
        must always call the super class implementations too!
    
    ===========================================================================
    Methods to override to control the media player:
    ===========================================================================
    
        * ctrl_toggle_playing()
        * ctrl_toggle_repeat()
        * ctrl_toggle_shuffle()
        * ctrl_toggle_fullscreen()
        * ctrl_next()
        * ctrl_previous()
        * ctrl_seek()
        * ctrl_volume()
        * ctrl_rate()
        * ctrl_tag()
        * ctrl_navigate()
        
        * action_files()
        * action_playlist_item()
        * action_queue_item()
        * action_mlib_item()
        * action_mlib_list()
        * action_search_item()
        
        Player adapters only need to implement only a *subset* of these
        methods - depending on what is possible and what makes sense.
        
        Remuco checks which methods have been overridden and uses this
        information to notify Remuco clients about capabilities of player
        adapters. 

    ===========================================================================
    Methods to override to provide information from the media player:
    ===========================================================================
    
        * request_playlist()
        * request_queue()
        * request_mlib()
        * request_search()
    
        As above, only override the methods which make sense for the
        corresponding media player.
    
    ===========================================================================
    Methods to call to synchronize media player state information with clients:
    ===========================================================================
    
        * update_playback()
        * update_repeat()
        * update_shuffle()
        * update_item()
        * update_position()
        * update_progress()
        
        These methods should be called whenever the corresponding information
        has changed in the media player (it is safe to call these methods also
        if there actually is no change, internally a change check is done
        before sending any data to clients).
        
        Subclasses of PlayerAdapter may override the method poll() to
        periodically check a player's state.
        
    ===========================================================================
    Finally some utility methods:
    ===========================================================================
    
        * find_image()
        
    '''
    
    manager = NoManager()

    # =========================================================================
    # constructor 
    # =========================================================================
    
    def __init__(self, name, playback_known=False, volume_known=False,
                 repeat_known=False, shuffle_known=False, progress_known=False,
                 max_rating=0, poll=2.5, file_actions=None, mime_types=None,
                 search_mask=None):
        """Create a new player adapter and configure its capabilities.
        
        Just does some early initializations. Real job starts with start().
        
        @param name:
            name of the media player
        @keyword playback_known:
            indicates if the player's playback state can be provided (see
            update_playback())
        @keyword volume_known:
            indicates if the player's volume can be provided (see
            update_volume())
        @keyword repeat_known:
            indicates if the player's repeat mode can be provided (see
            update_repeat())
        @keyword shuffle_known:
            indicates if the player's shuffle mode can be provided (see
            update_shuffle())
        @keyword progress_known:
            indicates if the player's playback progress can be provided (see
            update_progress())
        @keyword max_rating:
            maximum possible rating value for items
        @keyword poll:
            interval in seconds to call poll()
        @keyword file_actions:
            list of ItemAction which can be applied to files from the local
            file system (actions like play a file or append files to the
            playlist) - this keyword is only relevant if the method
            action_files() gets overridden
        @keyword mime_types:
            list of mime types specifying the files to which the actions given
            by the keyword 'file_actions' can be applied, this may be general
            types like 'audio' or 'video' but also specific types like
            'audio/mp3' or 'video/quicktime' (setting this to None means all
            mime types are supported) - this keyword is only relevant if the
            method action_files() gets overridden
        @keyword search_mask:
             list of fields to search the players library for (e.g. artist,
             genre, any, ...) - if set method request_search() should be
             overridden
        
        @attention: When overriding, call super class implementation first!
        
        """
        
        self.__name = name
        
        # init config (config inits logging)
        
        self.config = config.Config(self.__name)
        
        # init misc fields
        
        serial.Bin.HOST_ENCODING = self.config.player_encoding
        
        self.__clients = []
        
        self.__state = PlayerState()
        self.__progress = Progress()
        self.__item_id = None
        self.__item_info = None
        self.__item_img = None
        
        flags = self.__util_calc_flags(playback_known, volume_known,
            repeat_known, shuffle_known, progress_known)
        
        self.__info = PlayerInfo(name, flags, max_rating, file_actions,
                                 search_mask)
        
        self.__sync_triggers = {}
        
        self.__poll_ival = max(500, int(poll * 1000))
        self.__poll_sid = 0
        
        self.stopped = True
        
        self.__server_bluetooth = None
        self.__server_wifi = None
        
        if self.config.fb_root_dirs:
            self.__filelib = files.FileSystemLibrary(
                self.config.fb_root_dirs, mime_types,
                self.config.fb_show_extensions, False)
        else:
            log.info("file browser is disabled")
            
        if "REMUCO_TESTSHELL" in os.environ:
            from remuco import testshell
            testshell.setup(self)

        log.debug("init done")
    
    def start(self):
        """Start the player adapter.
        
        @attention: When overriding, call super class implementation first!
        
        """
        
        if not self.stopped:
            log.debug("ignore start, already running")
            return
        
        self.stopped = False
        
        # set up server
        
        if self.config.bluetooth_enabled:
            self.__server_bluetooth = net.BluetoothServer(self.__clients,
                    self.__info, self.__handle_message, self.config)
        else:
            self.__server_bluetooth = None

        if self.config.wifi_enabled:
            self.__server_wifi = net.WifiServer(self.__clients,
                    self.__info, self.__handle_message, self.config)
        else:
            self.__server_wifi = None
            
        # set up polling
        
        if self.__poll_ival > 0:
            log.debug("poll every %d milli seconds" % self.__poll_ival)
            self.__poll_sid = GObject.timeout_add(self.__poll_ival, self.__poll)
            
        
        log.debug("start done")
    
    def stop(self):
        """Shutdown the player adapter.
        
        Disconnects all clients and shuts down the Bluetooth and WiFi server.
        Also ignores any subsequent calls to an update or reply method (e.g.
        update_volume(), ..., reply_playlist_request(), ...). 
        
        @note: The same player adapter instance can be started again with
            start().

        @attention: When overriding, call super class implementation first!
        
        """

        if self.stopped: return
        
        self.stopped = True
        
        for c in self.__clients:
            c.disconnect(remove_from_list=False, send_bye_msg=True)
            
        self.__clients = []
        
        if self.__server_bluetooth is not None:
            self.__server_bluetooth.down()
            self.__server_bluetooth = None
        if self.__server_wifi is not None:
            self.__server_wifi.down()
            self.__server_wifi = None
            
        for sid in self.__sync_triggers.values():
            if sid is not None:
                GObject.source_remove(sid)
                
        self.__sync_triggers = {}

        if self.__poll_sid > 0:
            GObject.source_remove(self.__poll_sid)
            
        log.debug("stop done")
    
    def poll(self):
        """Does nothing by default.
        
        If player adapters override this method, it gets called periodically
        in the interval specified by the keyword 'poll' in __init__().
        
        A typical use case of this method is to detect the playback progress of
        the current item and then call update_progress(). It can also be used
        to poll any other player state information when a player does not
        provide signals for all or certain state information changes.
        
        """
        raise NotImplementedError
    
    def __poll(self):
        
        if self.config.master_volume_enabled:
            self.__update_volume_master()
        
        try:
            self.poll()
        except NotImplementedError:
            # poll again if master volume is used, otherwise not
            return self.config.master_volume_enabled
        
        return True
    
    # =========================================================================
    # utility methods which may be useful for player adapters
    # =========================================================================
    
    def find_image(self, resource):
        """Find a local art image file related to a resource.
        
        This method first looks in the resource' folder for typical art image
        files (e.g. 'cover.png', 'front.jpg', ...). If there is no such file it
        then looks into the user's thumbnail directory (~/.thumbnails).
        
        @param resource:
            resource to find an art image for (may be a file name or URI)
        @keyword prefer_thumbnail:
            True means first search in thumbnails, False means first search in
            the resource' folder
                                   
        @return: an image file name (which can be used for update_item()) or
            None if no image file has been found or if 'resource' is not local
        
        """
        
        file = art.get_art(resource)
        log.debug("image for '%s': %s" % (resource, file))
        return file
    
    # =========================================================================
    # control interface 
    # =========================================================================
    
    def ctrl_toggle_playing(self):
        """Toggle play and pause. 
        
        @note: Override if it is possible and makes sense.
        
        """
        log.error("** BUG ** in feature handling")
    
    def ctrl_toggle_repeat(self):
        """Toggle repeat mode. 
        
        @note: Override if it is possible and makes sense.
        
        @see: update_repeat()
               
        """
        log.error("** BUG ** in feature handling")
    
    def ctrl_toggle_shuffle(self):
        """Toggle shuffle mode. 
        
        @note: Override if it is possible and makes sense.
        
        @see: update_shuffle()
               
        """
        log.error("** BUG ** in feature handling")
    
    def ctrl_toggle_fullscreen(self):
        """Toggle full screen mode. 
        
        @note: Override if it is possible and makes sense.
        
        """
        log.error("** BUG ** in feature handling")

    def ctrl_next(self):
        """Play the next item. 
        
        @note: Override if it is possible and makes sense.
        
        """
        log.error("** BUG ** in feature handling")
    
    def ctrl_previous(self):
        """Play the previous item. 
        
        @note: Override if it is possible and makes sense.
        
        """
        log.error("** BUG ** in feature handling")
    
    def ctrl_seek(self, direction):
        """Seek forward or backward some seconds. 
        
        The number of seconds to seek should be reasonable for the current
        item's length (if known).
        
        If the progress of the current item is known, it should get
        synchronized immediately with clients by calling update_progress().
        
        @param direction:
            * -1: seek backward 
            * +1: seek forward
        
        @note: Override if it is possible and makes sense.
        
        """
        log.error("** BUG ** in feature handling")
    
    def ctrl_rate(self, rating):
        """Rate the currently played item. 
        
        @param rating:
            rating value (int)
        
        @note: Override if it is possible and makes sense.
        
        """
        log.error("** BUG ** in feature handling")
    
    def ctrl_tag(self, id, tags):
        """Attach some tags to an item.
        
        @param id:
            ID of the item to attach the tags to
        @param tags:
            a list of tags
        
        @note: Tags does not mean ID3 tags or similar. It means the general
            idea of tags (e.g. like used at last.fm). 

        @note: Override if it is possible and makes sense.
               
        """
        log.error("** BUG ** in feature handling")

    def ctrl_navigate(self, action):
        """Navigate through menus (typically DVD menus).

        @param action:
            A number selecting one of these actions: UP, DOWN, LEFT, RIGHT,
            SELECT, RETURN, TOPMENU (e.g. 0 is UP and 6 is TOPMENU).

        @note: Override if it is possible and makes sense.
        
        """
        log.error("** BUG ** in feature handling")
    
    def ctrl_volume(self, direction):
        """Adjust volume. 
        
        @param volume:
            * -1: decrease by some percent (5 is a good value)
            *  0: mute volume
            * +1: increase by some percent (5 is a good value)
        
        @note: Override if it is possible and makes sense.
               
        """
        log.error("** BUG ** in feature handling")
        
    def __ctrl_volume_master(self, direction):
        """Adjust volume using custom volume command (instead of player)."""
        
        if direction < 0:
            cmd = self.config.master_volume_down_cmd
        elif direction > 0:
            cmd = self.config.master_volume_up_cmd
        else:
            cmd = self.config.master_volume_mute_cmd
        
        ret, out = subprocess.getstatusoutput("sh -c '%s'" % cmd)
        if ret != os.EX_OK:
            log.error("master-volume-... failed: %s" % out)
        else:
            GObject.idle_add(self.__update_volume_master)
        
    def __ctrl_shutdown_system(self):
        
        if self.config.system_shutdown_enabled:
            log.debug("run system shutdown command")
            cmd = "sh -c '%s'" % self.config.system_shutdown_cmd
            ret, out = subprocess.getstatusoutput(cmd)
            if ret != os.EX_OK:
                log.error("system-shutdown failed: %s" % out)
                return
            self.stop()

    # =========================================================================
    # actions interface
    # =========================================================================
    
    def action_files(self, action_id, files, uris):
        """Do an action on one or more files.
        
        The files are specified redundantly by 'files' and 'uris' - use
        whatever fits better. If the specified action is not applicable to
        multiple files, then 'files' and 'uris' are one element lists.
         
        The files in 'files' and 'uris' may be any files from the local file
        system that have one of the mime types specified by the keyword
        'mime_types' in __init__().
        
        @param action_id:
            ID of the action to do - this specifies one of the actions passed
            previously to __init__() by the keyword 'file_actions'
        @param files:
            list of files to apply the action to (regular path names) 
        @param uris:
            list of files to apply the action to (URI notation) 
        
        @note: Override if file item actions gets passed to __init__().
        
        """
        log.error("** BUG ** action_files() not implemented")
    
    def action_playlist_item(self, action_id, positions, ids):
        """Do an action on one or more items from the playlist.
        
        The items are specified redundantly by 'positions' and 'ids' - use
        whatever fits better. If the specified action is not applicable to
        multiple items, then 'positions' and 'ids' are one element lists. 
        
        @param action_id:
            ID of the action to do - this specifies one of the actions passed
            previously to reply_playlist_request() by the keyword 'item_actions'
        @param positions:
            list of positions to apply the action to
        @param ids:
            list of IDs to apply the action to

        @note: Override if item actions gets passed to reply_playlist_request().
        
        """
        log.error("** BUG ** action_item() not implemented")
    
    def action_queue_item(self, action_id, positions, ids):
        """Do an action on one or more items from the play queue.
        
        The items are specified redundantly by 'positions' and 'ids' - use
        whatever fits better. If the specified action is not applicable to
        multiple items, then 'positions' and 'ids' are one element lists. 
        
        @param action_id:
            ID of the action to do - this specifies one of the actions passed
            previously to reply_queue_request() by the keyword 'item_actions'
        @param positions:
            list of positions to apply the action to 
        @param ids:
            list of IDs to apply the action to

        @note: Override if item actions gets passed to reply_queue_request().
        
        """
        log.error("** BUG ** action_item() not implemented")
    
    def action_mlib_item(self, action_id, path, positions, ids):
        """Do an action on one or more items from the player's media library.
        
        The items are specified redundantly by 'positions' and 'ids' - use
        whatever fits better. If the specified action is not applicable to
        multiple items, then 'positions' and 'ids' are one element lists. 
        
        @param action_id:
            ID of the action to do - this specifies one of the actions passed
            previously to reply_mlib_request() by the keyword 'item_actions'
        @param path:
            the library path that contains the items
        @param positions:
            list of positions to apply the action to 
        @param ids:
            list of IDs to apply the action to

        @note: Override if item actions gets passed to reply_mlib_request().
                
        """
        log.error("** BUG ** action_mlib_item() not implemented")
    
    def action_mlib_list(self, action_id, path):
        """Do an action on a list from the player's media library.
        
        @param action_id:
            ID of the action to do - this specifies one of the actions passed
            previously to reply_mlib_request() by the keyword 'list_actions'
        @param path:
            path specifying the list to apply the action to
            
        @note: Override if list actions gets passed to reply_mlib_request().
                
        """
        log.error("** BUG ** action_mlib_list() not implemented")
    
    def action_search_item(self, action_id, positions, ids):
        """Do an action on one or more items from a search result.
        
        @param action_id:
            ID of the action to do - this specifies one of the actions passed
            previously to reply_search_request() by the keyword 'item_actions'
        @param positions:
            list of positions to apply the action to 
        @param ids:
            list of IDs to apply the action to
            
        @note: Override if list actions gets passed to reply_search_request().
                
        """
        log.error("** BUG ** action_search_item() not implemented")
    
    # =========================================================================
    # request interface 
    # =========================================================================
    
    def request_playlist(self, reply):
        """Request the content of the currently active playlist.
        
        @param reply:
            a ListReply object
        
        @note: Override if it is possible and makes sense.
               
        """
        log.error("** BUG ** in feature handling")

    def request_queue(self, reply):
        """Request the content of the play queue.
        
        @param reply:
            a ListReply object
        
        @note: Override if it is possible and makes sense.
               
        """
        log.error("** BUG ** in feature handling")

    def request_mlib(self, reply, path):
        """Request the content of a playlist from the player's media library.
        
        @param reply:
            a ListReply object
        @param path: 
            a path within a player's media library
            
        If path is an empty list, the root of the library (all top level
        playlists) are requested. Otherwise path is set as illustrated in this
        example:

        Consider a player with a media library structure like this:

           |- Radio
           |- Genres
              |- Jazz
              |- ...
           |- Dynamic
              |- Never played
              |- Played recently
              |- ...
           |- Playlists
              |- Party
                 |- Sue's b-day
                 |- ...
           |- ...

        If path is the empty list, all top level playlists are requests, e.g.
        ['Radio', 'Genres', 'Dynamic', 'Playlists', ...]. Otherwise path may
        specify a specific level in the library tree, e.g. [ 'Radio' ] or
        [ 'Playlists', 'Party', 'Sue's b-day' ] or etc.
    
        @note: Override if it is possible and makes sense.
               
        """
        log.error("** BUG ** in feature handling")
    
    def request_search(self, reply, query):
        """Request a list of items matching a search query.
        
        @param reply:
            a ListReply object
        @param query:
            a list of search query values corresponding with the search mask
            specified with keyword 'search_mask' in PlayerAdapter.__init__()
            
        Example: If search mask was [ 'Artist', 'Title', 'Album' ], then
        a query may look like this: [ 'Blondie', '', 'Best' ]. It is up to
        player adapters how to interpret these values. However, good practice
        is to interpret them as case insensitive, and-connected, non exact
        matching search values. The given example would then reply a list
        with all items where 'Blondie' is contained in the artist field and
        'Best' is contained in the Album field.
        
        @note: Override if it is possible and makes sense.
               
        """
        log.error("** BUG ** in feature handling")
    
    # =========================================================================
    # player side synchronization
    # =========================================================================    

    def update_position(self, position, queue=False):
        """Set the current item's position in the playlist or queue. 
        
        @param position:
            position of the currently played item (starting at 0)
        @keyword queue:
            True if currently played item is from the queue, False if it is
            from the currently active playlist
                        
        @note: Call to synchronize player state with remote clients.
        
        """
        change = self.__state.queue != queue
        change |= self.__state.position != position
        
        if change:
            self.__state.queue = queue
            self.__state.position = position
            self.__sync_trigger(self.__sync_state)
        
    def update_playback(self, playback):
        """Set the current playback state.
        
        @param playback:
            playback mode
            
        @see: remuco.PLAYBACK_...
        
        @note: Call to synchronize player state with remote clients.
        
        """
        change = self.__state.playback != playback
        
        if change:
            self.__state.playback = playback
            self.__sync_trigger(self.__sync_state)
    
    def update_repeat(self, repeat):
        """Set the current repeat mode. 
        
        @param repeat: True means play indefinitely, False means stop after the
            last playlist item
        
        @note: Call to synchronize player state with remote clients.
        
        """
        repeat = bool(repeat)
        
        change = self.__state.repeat != repeat
        
        if change:
            self.__state.repeat = repeat
            self.__sync_trigger(self.__sync_state)
    
    def update_shuffle(self, shuffle):
        """Set the current shuffle mode. 
        
        @param shuffle: True means play in non-linear order, False means play
            in linear order
        
        @note: Call to synchronize player state with remote clients.
        
        """
        shuffle = bool(shuffle)
        
        change = self.__state.shuffle != shuffle
        
        if change:
            self.__state.shuffle = shuffle
            self.__sync_trigger(self.__sync_state)
    
    def update_volume(self, volume):
        """Set the current volume.
        
        @param volume: the volume in percent
        
        @note: Call to synchronize player state with remote clients.
        
        """
        if self.config.master_volume_enabled:
            # ignore if custom command has been set
            return
        
        volume = int(volume)
        
        if volume < 0 or volume > 100:
            log.warning("bad volume from player adapter: %d" % volume)
            volume = 50
            
        change = self.__state.volume != volume
        
        if change:
            self.__state.volume = volume
            self.__sync_trigger(self.__sync_state)
    
    def __update_volume_master(self):
        """Set the current volume (use custom command instead of player)."""

        cmd = "sh -c '%s'" % self.config.master_volume_get_cmd
        ret, out = subprocess.getstatusoutput(cmd)
        if ret != os.EX_OK:
            log.error("master-volume-get failed: '%s'" % out)
            return
        try:
            volume = int(out)
            if volume < 0 or volume > 100:
                raise ValueError
        except ValueError:
            log.error("output of master-volume-get malformed: '%s'" % out)
            return
        
        change = self.__state.volume != volume
        
        if change:
            self.__state.volume = volume
            self.__sync_trigger(self.__sync_state)
    
    def update_progress(self, progress, length):
        """Set the current playback progress.
        
        @param progress:
            number of currently elapsed seconds
        @keyword length:
            item length in seconds (maximum possible progress value)
        
        @note: Call to synchronize player state with remote clients.
        
        """
        # sanitize progress (to a multiple of 5)
        length = max(0, int(length))
        progress = max(0, int(progress))
        off = progress % 5
        if off < 3:
            progress -= off
        else:
            progress += (5 - off)
        if length > 0:
            progress = min(length, progress)
        
        change = self.__progress.length != length
        change |= self.__progress.progress != progress
        
        if change:
            self.__progress.progress = progress
            self.__progress.length = length
            self.__sync_trigger(self.__sync_progress)
    
    def update_item(self, id, info, img):
        """Set currently played item.
        
        @param id:
            item ID (str)
        @param info:
            meta information (dict)
        @param img:
            image / cover art (either a file name or URI or an instance of
            Image.Image)
        
        @note: Call to synchronize player state with remote clients.

        @see: find_image() for finding image files for an item.
        
        @see: remuco.INFO_... for keys to use for 'info'
               
        """
        
        log.debug("new item: (%s, %s %s)" % (id, info, img))
        
        change = self.__item_id != id
        change |= self.__item_info != info
        change |= self.__item_img != img
        
        if change:
            self.__item_id = id
            self.__item_info = info
            self.__item_img = img
            self.__sync_trigger(self.__sync_item)
            
    # =========================================================================
    # synchronization (outbound communication)
    # =========================================================================
    
    def __sync_trigger(self, sync_fn):
        
        if self.stopped:
            return
        
        if sync_fn in self.__sync_triggers:
            log.debug("trigger for %s already active" % sync_fn.__name__)
            return
        
        self.__sync_triggers[sync_fn] = \
            GObject.idle_add(sync_fn, priority=GObject.PRIORITY_LOW)
        
    def __sync_state(self):
        
        del self.__sync_triggers[self.__sync_state]

        log.debug("broadcast new state to clients: %s" % self.__state)
        
        msg = net.build_message(message.SYNC_STATE, self.__state)
        
        if msg is None:
            return
        
        for c in self.__clients: c.send(msg)
        
        return False
    
    def __sync_progress(self):
        
        del self.__sync_triggers[self.__sync_progress]
        
        log.debug("broadcast new progress to clients: %s" % self.__progress)
        
        msg = net.build_message(message.SYNC_PROGRESS, self.__progress)
        
        if msg is None:
            return
        
        for c in self.__clients: c.send(msg)
        
        return False
    
    def __sync_item(self):

        del self.__sync_triggers[self.__sync_item]
        
        log.debug("broadcast new item to clients: %s" % self.__item_id)
        
        for c in self.__clients:
            
            msg = net.build_message(message.SYNC_ITEM, self.__item(c))
            
            if msg is not None:
                c.send(msg)
        
        return False
    
    # =========================================================================
    # handling client message (inbound communication)
    # =========================================================================
    
    def __handle_message(self, client, id, bindata):
        
        if message.is_control(id):

            log.debug("control from client %s" % client)

            self.__handle_message_control(id, bindata)
            
        elif message.is_action(id):

            log.debug("action from client %s" % client)

            self.__handle_message_action(id, bindata)
            
        elif message.is_request(id):
            
            log.debug("request from client %s" % client)

            self.__handle_message_request(client, id, bindata)
            
        elif id == message.PRIV_INITIAL_SYNC:
            
            msg = net.build_message(message.SYNC_STATE, self.__state)
            client.send(msg)
            
            msg = net.build_message(message.SYNC_PROGRESS, self.__progress)
            client.send(msg)
            
            msg = net.build_message(message.SYNC_ITEM, self.__item(client))
            client.send(msg)
            
        else:
            log.error("** BUG ** unexpected message: %d" % id)
    
    def __handle_message_control(self, id, bindata):
    
        if id == message.CTRL_PLAYPAUSE:
            
            self.ctrl_toggle_playing()
            
        elif id == message.CTRL_NEXT:
            
            self.ctrl_next()
            
        elif id == message.CTRL_PREV:
            
            self.ctrl_previous()
            
        elif id == message.CTRL_SEEK:
            
            control = serial.unpack(Control, bindata)
            if control is None:
                return
            
            self.ctrl_seek(control.param)
            
        elif id == message.CTRL_VOLUME:
            
            control = serial.unpack(Control, bindata)
            if control is None:
                return
            
            if self.config.master_volume_enabled:
                self.__ctrl_volume_master(control.param)
            else:
                self.ctrl_volume(control.param)
            
        elif id == message.CTRL_REPEAT:
            
            self.ctrl_toggle_repeat()
            
        elif id == message.CTRL_SHUFFLE:
            
            self.ctrl_toggle_shuffle()

        elif id == message.CTRL_RATE:
            
            control = serial.unpack(Control, bindata)
            if control is None:
                return
            
            self.ctrl_rate(control.param)
            
        elif id == message.CTRL_TAG:
            
            tag = serial.unpack(Tagging, bindata)
            if tag is None:
                return
            
            self.ctrl_tag(tag.id, tag.tags)

        elif id == message.CTRL_NAVIGATE:
            control = serial.unpack(Control, bindata)
            if control is None:
                return

            self.ctrl_navigate(control.param)

        elif id == message.CTRL_FULLSCREEN:
            
            self.ctrl_toggle_fullscreen()

        elif id == message.CTRL_SHUTDOWN:
            
            self.__ctrl_shutdown_system()
            
        else:
            log.error("** BUG ** unexpected control message: %d" % id)
            
    def __handle_message_action(self, id, bindata):
        
        a = serial.unpack(Action, bindata)
        if a is None:
            return
        
        if id == message.ACT_PLAYLIST:
            
            self.action_playlist_item(a.id, a.positions, a.items)
            
        elif id == message.ACT_QUEUE:
            
            self.action_queue_item(a.id, a.positions, a.items)
            
        elif id == message.ACT_MLIB and a.id < 0: # list action id
            
            self.action_mlib_list(a.id, a.path)
                
        elif id == message.ACT_MLIB and a.id > 0: # item action id
            
            self.action_mlib_item(a.id, a.path, a.positions, a.items)
                
        elif id == message.ACT_FILES:
        
            uris = self.__util_files_to_uris(a.items)
            
            self.action_files(a.id, a.items, uris)
        
        elif id == message.ACT_SEARCH:
            
            self.action_search_item(a.id, a.positions, a.items)
            
        else:
            log.error("** BUG ** unexpected action message: %d" % id)
            
    def __handle_message_request(self, client, id, bindata):

        request = serial.unpack(Request, bindata)    
        if request is None:
            return
        
        reply = ListReply(client, request.request_id, id, request.page,
                          path=request.path)
        
        if id == message.REQ_PLAYLIST:
            
            self.request_playlist(reply)
            
        elif id == message.REQ_QUEUE:
            
            self.request_queue(reply)
            
        elif id == message.REQ_MLIB:
            
            self.request_mlib(reply, request.path)
            
        elif id == message.REQ_FILES:
            
            reply.nested, reply.ids, reply.names = \
                self.__filelib.get_level(request.path)
            
            reply.send()
            
        elif id == message.REQ_SEARCH:
            
            self.request_search(reply, request.path)
            
        else:
            log.error("** BUG ** unexpected request message: %d" % id)
            
    # =========================================================================
    # miscellaneous 
    # =========================================================================
    
    def __item(self, client):
        """Creates a client specific item object."""
        
        return Item(self.__item_id, self.__item_info, self.__item_img,
                    client.info.img_size, client.info.img_type)
        
    def __util_files_to_uris(self, files):
        
        def file_to_uri(file):
            url = urllib.pathname2url(file)
            return urllib.parse.urlunparse(("file", None, url, None, None, None))
        
        if not files:
            return []
        
        uris = []
        for file in files:
            uris.append(file_to_uri(file))
            
        return uris
    
    def __util_calc_flags(self, playback_known, volume_known, repeat_known,
                          shuffle_known, progress_known):
        """Check player adapter capabilities.
        
        Most capabilities get detected by testing which methods have been
        overridden by a subclassing player adapter.
        
        """ 
        
        def ftc(cond, feature):
            if inspect.ismethod(cond): # check if overridden
                enabled = cond.__module__ != __name__
            else:
                enabled = cond
            if enabled:
                return feature
            else:
                return 0  
        
        features = (
                                           
            # --- 'is known' features ---
            
            ftc(playback_known, FT_KNOWN_PLAYBACK),
            ftc(volume_known, FT_KNOWN_VOLUME),
            ftc(self.config.master_volume_enabled, FT_KNOWN_VOLUME),
            ftc(repeat_known, FT_KNOWN_REPEAT),
            ftc(shuffle_known, FT_KNOWN_SHUFFLE),
            ftc(progress_known, FT_KNOWN_PROGRESS),

            # --- misc control features ---

            ftc(self.ctrl_toggle_playing, FT_CTRL_PLAYBACK),
            ftc(self.ctrl_volume, FT_CTRL_VOLUME),
            ftc(self.config.master_volume_enabled, FT_CTRL_VOLUME),
            ftc(self.ctrl_seek, FT_CTRL_SEEK),
            ftc(self.ctrl_tag, FT_CTRL_TAG),
            ftc(self.ctrl_rate, FT_CTRL_RATE),
            ftc(self.ctrl_toggle_repeat, FT_CTRL_REPEAT),
            ftc(self.ctrl_toggle_shuffle, FT_CTRL_SHUFFLE),
            ftc(self.ctrl_next, FT_CTRL_NEXT),
            ftc(self.ctrl_previous, FT_CTRL_PREV),
            ftc(self.ctrl_toggle_fullscreen, FT_CTRL_FULLSCREEN),
            ftc(self.ctrl_navigate, FT_CTRL_NAVIGATE),
        
            # --- request features ---

            ftc(self.request_playlist, FT_REQ_PL),
            ftc(self.request_queue, FT_REQ_QU),
            ftc(self.request_mlib, FT_REQ_MLIB),

            ftc(self.config.system_shutdown_enabled, FT_SHUTDOWN),
        
        )
        
        flags = 0
        
        for feature in features:
            flags |= feature
             
        log.debug("flags: %X" % flags)
        
        return flags
