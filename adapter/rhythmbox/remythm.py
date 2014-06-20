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

"""Rhythmbox player adapter for Remuco, implemented as a Rhythmbox plugin."""

import time
import dbus
from dbus.exceptions import DBusException

from gi.repository import GConf, GObject, Peas
from gi.repository import RB

import rb

import remuco
from remuco import log

# =============================================================================
# rhythmbox dbus names
# =============================================================================

DBUS_NAME = "org.mpris.MediaPlayer2.rhythmbox"
DBUS_PATH = "/org/mpris/MediaPlayer2"
DBUS_IFACE = "org.mpris.MediaPlayer2.Player"
DBUS_IFACE_PROPS = "org.freedesktop.DBus.Properties"

# =============================================================================
# plugin
# =============================================================================

class RemucoPlugin(GObject.Object, Peas.Activatable):
    object = GObject.property (type = GObject.Object)
    
    def __init__(self):
        
        GObject.Object.__init__(self)
        
        self.__rba = None
        
    def do_activate(self):
        
        shell = self.object

        if self.__rba is not None:
            return
        
        print("create RhythmboxAdapter")
        self.__rba = RhythmboxAdapter()
        print("RhythmboxAdapter created")

        print("start RhythmboxAdapter")
        self.__rba.start(shell)
        print("RhythmboxAdapter started")
        
    def do_deactivate(self):

        shell = self.object
    
        if self.__rba is None:
            return
        
        print("stop RhythmboxAdapter")
        self.__rba.stop()
        print("RhythmboxAdapter stopped")
        
        self.__rba = None
        
# =============================================================================
# constants
# =============================================================================

PLAYORDER_SHUFFLE = "shuffle"
PLAYORDER_SHUFFLE_ALT = "random-by-age-and-rating"
PLAYORDER_REPEAT = "linear-loop"
PLAYORDER_NORMAL = "linear"

PLAYERORDER_TOGGLE_MAP_REPEAT = {
    PLAYORDER_SHUFFLE: PLAYORDER_SHUFFLE_ALT,
    PLAYORDER_SHUFFLE_ALT: PLAYORDER_SHUFFLE,
    PLAYORDER_REPEAT: PLAYORDER_NORMAL,
    PLAYORDER_NORMAL: PLAYORDER_REPEAT
}

PLAYERORDER_TOGGLE_MAP_SHUFFLE = {
    PLAYORDER_SHUFFLE: PLAYORDER_NORMAL,
    PLAYORDER_NORMAL: PLAYORDER_SHUFFLE,
    PLAYORDER_SHUFFLE_ALT: PLAYORDER_REPEAT,
    PLAYORDER_REPEAT: PLAYORDER_SHUFFLE_ALT
}

SEARCH_MASK = ("Any", "Artist", "Title", "Album", "Genre")
SEARCH_PROPS = ("Any", RB.RhythmDBPropType.ARTIST, RB.RhythmDBPropType.TITLE,
                RB.RhythmDBPropType.ALBUM, RB.RhythmDBPropType.GENRE)
SEARCH_PROPS_ANY = (RB.RhythmDBPropType.ARTIST, RB.RhythmDBPropType.TITLE,
                    RB.RhythmDBPropType.ALBUM, RB.RhythmDBPropType.GENRE,
                    RB.RhythmDBPropType.LOCATION)

# =============================================================================
# actions
# =============================================================================

IA_JUMP = remuco.ItemAction("Jump to")
IA_REMOVE = remuco.ItemAction("Remove", multiple=True)
LA_PLAY = remuco.ListAction("Play")
IA_ENQUEUE = remuco.ItemAction("Enqueue", multiple=True)

PLAYLIST_ACTIONS = (IA_JUMP, IA_ENQUEUE)
QUEUE_ACTIONS = (IA_JUMP, IA_REMOVE)
MLIB_LIST_ACTIONS = (LA_PLAY,)
MLIB_ITEM_ACTIONS = (IA_ENQUEUE, IA_JUMP)
SEARCH_ACTIONS = (IA_ENQUEUE,)

# =============================================================================
# player adapter
# =============================================================================

class RhythmboxAdapter(remuco.PlayerAdapter):

    def __init__(self):
        
        self.__shell = None
        self.__gconf = None
        
        remuco.PlayerAdapter.__init__(self, "Rhythmbox",
                                      max_rating=5,
                                      playback_known=True,
                                      volume_known=True,
                                      repeat_known=True,
                                      shuffle_known=True,
                                      progress_known=True,
                                      search_mask=SEARCH_MASK)
        
        self.__item_id = None
        self.__item_entry = None
        self.__playlist_sc = None
        self.__queue_sc = None
        
        self.__signal_ids = ()
        
        log.debug("init done")

    def start(self, shell):
        
        if self.__shell is not None:
            log.warning("already started")
            return
        
        remuco.PlayerAdapter.start(self)
        
        self.__shell = shell
        
        sp = self.__shell.props.shell_player
        
        # gconf is used to adjust repeat and shuffle
        self.__gconf = GConf.Client.get_default()
        
        # shortcuts to RB data 
        
        self.__item_id = None
        self.__item_entry = None
        self.__playlist_sc = sp.get_playing_source()
        self.__queue_sc = self.__shell.props.queue_source
        
        # connect to shell player signals

        self.__signal_ids = (
            sp.connect("playing_changed", self.__notify_playing_changed),
            sp.connect("playing_uri_changed", self.__notify_playing_uri_changed),
            sp.connect("playing-source-changed", self.__notify_source_changed)
        )

        # state sync will happen by timeout
        # trigger item sync:
        self.__notify_playing_uri_changed(sp, sp.get_playing_entry()) # item sync
        
        log.debug("start done")

    def stop(self):
        
        remuco.PlayerAdapter.stop(self)

        if self.__shell is None:
            return

        # disconnect from shell player signals

        sp = self.__shell.props.shell_player

        for sid in self.__signal_ids:
            sp.disconnect(sid)
            
        self.__signal_ids = ()

        # release shell
        
        self.__shell = None
        self.__gconf = None
        
        log.debug("stop done")
        
    def poll(self):
        
        sp = self.__shell.props.shell_player
        
        # check repeat and shuffle
        
        order = sp.props.play_order
        
        repeat = order == PLAYORDER_REPEAT or order == PLAYORDER_SHUFFLE_ALT
        self.update_repeat(repeat)
        
        shuffle = order == PLAYORDER_SHUFFLE or order == PLAYORDER_SHUFFLE_ALT
        self.update_shuffle(shuffle)
        
        # check volume

        volume = int(sp.get_volume()[1] * 100)
        self.update_volume(volume)
        
        # check progress
        
        try:
            progress = sp.get_playing_time()[1]
            length = sp.get_playing_song_duration()
        except GObject.GError:
            progress = 0
            length = 0 
        else:
            self.update_progress(progress, length)
        
    # =========================================================================
    # control interface
    # =========================================================================
    
    def ctrl_next(self):
        
        sp = self.__shell.props.shell_player
        
        try:
            sp.do_next()
        except GObject.GError as e:
            log.debug("do next failed: %s" % str(e))
    
    def ctrl_previous(self):
        
        sp = self.__shell.props.shell_player
        
        try:
            sp.set_playing_time(0)
            time.sleep(0.1)
            sp.do_previous()
        except GObject.GError as e:
            log.debug("do previous failed: %s" % str(e))
    
    def ctrl_rate(self, rating):
        
        if self.__item_entry is not None:
            db = self.__shell.props.db
            try:
                db.entry_set(self.__item_entry, RB.RhythmDBPropType.RATING, rating)
            except GObject.GError as e:
                log.debug("rating failed: %s" % str(e))
    
    def ctrl_toggle_playing(self):
        
        sp = self.__shell.props.shell_player
        
        try:
            sp.playpause(True)
        except GObject.GError as e:
            log.debug("toggle play pause failed: %s" % str(e))
                
    def ctrl_toggle_repeat(self):
        
        sp = self.__shell.props.shell_player
        
        now = sp.props.play_order
        
        next = PLAYERORDER_TOGGLE_MAP_REPEAT.get(now, now)

        prop = "None"
        if next == PLAYORDER_REPEAT:
            prop = "Playlist"

        # Why dbus is so slow from rhythmbox plugin?
        try:
            bus = dbus.SessionBus()
            proxy = bus.get_object(DBUS_NAME, DBUS_PATH)
            bs = dbus.Interface(proxy, DBUS_IFACE_PROPS)
            bs.Set(DBUS_IFACE, 'LoopStatus', prop)
        except DBusException as e:
            log.warning("dbus error: %s" % e)

        # update state within a short time (don't wait for scheduled poll)
        GObject.idle_add(self.poll)
        
    def ctrl_toggle_shuffle(self):
        
        sp = self.__shell.props.shell_player

        now = sp.props.play_order
        
        next = PLAYERORDER_TOGGLE_MAP_SHUFFLE.get(now, now)

        prop = False
        if next == PLAYORDER_SHUFFLE or next == PLAYORDER_SHUFFLE_ALT:
            prop = True

        # Why dbus is so slow from rhythmbox plugin?
        try:
            bus = dbus.SessionBus()
            proxy = bus.get_object(DBUS_NAME, DBUS_PATH)
            bs = dbus.Interface(proxy, DBUS_IFACE_PROPS)
            bs.Set(DBUS_IFACE, 'Shuffle', prop)
        except DBusException as e:
            raise StandardError("dbus error: %s" % e)

        # update state within a short time (don't wait for scheduled poll)
        GObject.idle_add(self.poll)
        
    def ctrl_seek(self, direction):
        
        sp = self.__shell.props.shell_player

        try:
            sp.seek(direction * 5)
        except GObject.GError as e:
            log.debug("seek failed: %s" % str(e))
        else:
            # update volume within a short time (don't wait for scheduled poll)
            GObject.idle_add(self.poll)    
    
    def ctrl_volume(self, direction):
        
        sp = self.__shell.props.shell_player
        
        if direction == 0:
            sp.set_volume(0)
        else:
            try:
                sp.set_volume_relative(direction * 0.05)
            except GObject.GError as e:
                log.debug("set volume failed: %s" % str(e))
        
        # update volume within a short time (don't wait for scheduled poll)
        GObject.idle_add(self.poll)
        
    # =========================================================================
    # action interface
    # =========================================================================
    
    def action_playlist_item(self, action_id, positions, ids):

        if action_id == IA_JUMP.id:
            
            try:
                self.__jump_in_plq(self.__playlist_sc, positions[0])
            except GObject.GError as e:
                log.debug("playlist jump failed: %s" % e)
        
        elif action_id == IA_ENQUEUE.id:
            
            self.__enqueue_items(ids)
        
        else:
            log.error("** BUG ** unexpected action: %d" % action_id)
    
    def action_queue_item(self, action_id, positions, ids):

        if action_id == IA_JUMP.id:
            
            try:
                self.__jump_in_plq(self.__queue_sc, positions[0])
            except GObject.GError as e:
                log.debug("queue jump failed: %s" % e)
    
        elif action_id == IA_REMOVE.id:
            
            for id in ids:
                self.__shell.remove_from_queue(id)
    
        else:
            log.error("** BUG ** unexpected action: %d" % action_id)
    
    def action_mlib_item(self, action_id, path, positions, ids):
        
        if action_id == IA_ENQUEUE.id:
            
            self.__enqueue_items(ids)
        
        if action_id == IA_JUMP.id:
            
            self.action_mlib_list(LA_PLAY.id, path)
            
            # delay jump, otherwise sync with clients sometimes fails
            GObject.timeout_add(100, self.action_playlist_item, IA_JUMP.id,
                                positions, ids)

        else:
            log.error("** BUG ** unexpected action: %d" % action_id)
    
    def action_mlib_list(self, action_id, path):
        
        if action_id == LA_PLAY.id:
            
            sc = self.__mlib_path_to_source(path)
            if sc is None:
                log.warning("no source for path %s" % path)
                return
            
            sp = self.__shell.props.shell_player
    
            if sc != self.__playlist_sc:
                try:
                    sp.set_selected_source(sc)
                    sp.set_playing_source(sc)
                    self.__jump_in_plq(sc, 0)
                except GObject.GError as e:
                    log.debug("switching source failed: %s" % str(e))
            
        else:
            log.error("** BUG ** unexpected action: %d" % action_id)
    
    def action_search_item(self, action_id, positions, ids):
        
        if action_id == IA_ENQUEUE.id:
            
            self.__enqueue_items(ids)
            
        else:
            log.error("** BUG ** unexpected action: %d" % action_id)
    
    # =========================================================================
    # request interface
    # =========================================================================
    
    def request_playlist(self, reply):
        
        if self.__playlist_sc is None:
            reply.send()
            return
        
        try:
            qm = self.__playlist_sc.get_entry_view().props.model 
            reply.ids, reply.names = self.__get_item_list_from_qmodel(qm)
        except GObject.GError as e:
            log.warning("failed to get playlist items: %s" % e)
        
        reply.item_actions = PLAYLIST_ACTIONS
        
        reply.send()    

    def request_queue(self, reply):
        
        sc = self.__queue_sc
        qm = sc.props.query_model

        try:
            reply.ids, reply.names = self.__get_item_list_from_qmodel(qm)
        except GObject.GError as e:
            log.warning("failed to get queue items: %s" % e)
        
        reply.item_actions = QUEUE_ACTIONS
        
        reply.send()    

    def request_mlib(self, reply, path):

        slm = self.__shell.props.library_source
        
        ### root ? ###
        
        if not path:
            for group in slm:
                group_name = group.props.name
                reply.nested.append(group_name)
            reply.send()
            return
        
        ### group ? ### Library, Playlists

        if len(path) == 1:
            for group in slm:
                group_name = group.props.name
                if path[0] == group_name:
                    for sc in group.iterchildren():
                        source_name = sc[2]
                        # FIXME: how to be l10n independent here?
                        if source_name.startswith("Play Queue"):
                            continue
                        if source_name.startswith("Import Error"):
                            continue
                        log.debug("append %s" % source_name)
                        reply.nested.append(source_name)
                    break
            reply.list_actions = MLIB_LIST_ACTIONS
            reply.send()
            return
            
        ### regular playlist (source) ! ### Library/???, Playlists/???
        
        sc = self.__mlib_path_to_source(path)

        if sc is None:
            reply.send()
            return
        
        qm = sc.get_entry_view().props.model
            
        try:
            reply.ids, reply.names = self.__get_item_list_from_qmodel(qm)
        except GObject.GError as e:
            log.warning("failed to list items: %s" % e)
        
        reply.item_actions = MLIB_ITEM_ACTIONS
        
        reply.send()
        
    def request_search(self, reply, query):
        
        def eval_entry(entry):
            match = True
            for key in query_stripped:
                if key == "Any":
                    props = SEARCH_PROPS_ANY
                else:
                    props = [key]
                for prop in props:
                    val = entry.get_string(prop).lower()
                    if val.find(query_stripped[key]) >= 0:
                        break
                else:
                    match = False
                    break
            if match:
                id, name = self.__get_list_item_from_entry(entry)
                reply.ids.append(id)
                reply.names.append(name)
        
        query_stripped = {} # stripped query dict
        
        for key, val in zip(SEARCH_PROPS, query):
            if val.strip():
                query_stripped[key] = val.lower()

        if query_stripped:
            db = self.__shell.props.db
            db.entry_foreach(eval_entry)
        
        reply.item_actions = SEARCH_ACTIONS
        
        reply.send()

    # ==========================================================================
    # callbacks
    # ==========================================================================
    
    def __notify_playing_uri_changed(self, sp, uri):
        """Shell player signal callback to handle an item change."""

        log.debug("playing uri changed: %s" % uri)
        
        db = self.__shell.props.db

        entry = sp.get_playing_entry()
        if entry is None:
            id = None
        else:
            id = entry.get_string(RB.RhythmDBPropType.LOCATION)
        
        self.__item_id = id
        self.__item_entry = entry
        
        if entry is not None and id is not None:

            info = self.__get_item_from_entry(entry)
    
            img_data = db.entry_request_extra_metadata(entry, "rb:coverArt")
            if img_data is None:
                img_file = self.find_image(id)
            else:
                try:
                    img_file = "%s/rhythmbox.cover" % self.config.cache
                    img_data.save(img_file, "png")
                except IOError as e:
                    log.warning("failed to save cover art (%s)" % e)
                    img_file = None
    
        else:
            id = None
            img_file = None
            info = None

        self.update_item(id, info, img_file)
        
        # a new item may result in a new position:
        pfq = self.__shell.props.shell_player.props.playing_from_queue
        self.update_position(self.__get_position(), queue=pfq)

    def __notify_playing_changed(self, sp, b):
        """Shell player signal callback to handle a change in playback."""
        
        log.debug("playing changed: %s" % str(b))
        
        if b:
            self.update_playback(remuco.PLAYBACK_PLAY)
        else:
            self.update_playback(remuco.PLAYBACK_PAUSE)

    def __notify_source_changed(self, sp, source_new):
        """Shell player signal callback to handle a playlist switch."""
        
        log.debug("source changed: %s" % str(source_new))
        
        self.__playlist_sc = source_new
        
    # =========================================================================
    # helper methods
    # =========================================================================

    def __jump_in_plq(self, sc, position):
        """Do a jump within the playlist or queue.
        
        @param sc:
            either current playlist or queue source
        @param position:
            position to jump to
            
        """

        if sc is None:
            return
        
        qm = sc.get_entry_view().props.model
        
        id_to_remove_from_queue = None
        
        sp = self.__shell.props.shell_player

        if sp.props.playing_from_queue:
            id_to_remove_from_queue = self.__item_id

        found = False
        i = 0
        for row in qm:
            if i == position:
                sp.set_selected_source(sc)
                sp.set_playing_source(sc)
                sp.play_entry(row[0], sc)
                found = True
                break
            i += 1
        
        if not found:
            sp.do_next()
        
        if id_to_remove_from_queue != None:
            log.debug("remove %s from queue" % id_to_remove_from_queue)
            self.__shell.remove_from_queue(id_to_remove_from_queue)

    def __get_item_list_from_qmodel(self, qmodel):
        """Get all items in a query model.
        
        @return: 2 lists - IDs and names of the items
        """
        
        ids = []
        names = []

        if qmodel is None:
            return (ids, names)

        for row in qmodel:
            id, name = self.__get_list_item_from_entry(row[0])
            ids.append(id)
            names.append(name)

        return (ids, names)
    
    def __get_list_item_from_entry(self, entry):
        """Get Remuco list item from a Rhythmbox entry.
        
        @return: ID and name
        """
        
        db = self.__shell.props.db

        id = entry.get_string(RB.RhythmDBPropType.LOCATION)
        
        artist = entry.get_string(RB.RhythmDBPropType.ARTIST)
        title = entry.get_string(RB.RhythmDBPropType.TITLE)
        
        if artist and title:
            name = "%s - %s" % (artist, title)
        else:
            name = title or artist or "Unknown"
        
        return id, name

    def __get_item_from_entry(self, entry):
        """Get a Remuco item from a Rhythmbox entry.
        
        @return: meta information (dictionary) - also if entry is None (in this
                 case dummy information is returned)
        """
        
        if entry is None:
            return { remuco.INFO_TITLE : "No information" }
        
        db = self.__shell.props.db
        
        meta = {
            remuco.INFO_TITLE : str(entry.get_string(RB.RhythmDBPropType.TITLE)),
            remuco.INFO_ARTIST: str(entry.get_string(RB.RhythmDBPropType.ARTIST)),
            remuco.INFO_ALBUM : str(entry.get_string(RB.RhythmDBPropType.ALBUM)),
            remuco.INFO_GENRE : str(entry.get_string(RB.RhythmDBPropType.GENRE)),
            #remuco.INFO_BITRATE : str(entry.get_string(RB.RhythmDBPropType.BITRATE)),
            #remuco.INFO_LENGTH : str(entry.get_string(RB.RhythmDBPropType.DURATION)),
            #remuco.INFO_RATING : str(int(entry.get_ulong(RB.RhythmDBPropType.RATING))),
            #remuco.INFO_TRACK : str(entry.get_string(RB.RhythmDBPropType.TRACK_NUMBER)),
            #remuco.INFO_YEAR : str(entry.get_string(RB.RhythmDBPropType.YEAR))
        }

        return meta 
    
    def __mlib_path_to_source(self, path):
        """Get the source object related to a library path.
        
        @param path: must contain the source' group and name (2 element list)
        """
        
        if len(path) != 2:
            log.error("** BUG ** invalid path length: %s" % path)
            return None
        
        group_name, source_name = path
        
        if group_name is None or source_name is None:
            return None
        
        slm = self.__shell.props.sourcelist_model
        
        for group in slm:
            if group_name == group:
                for source in group.iterchildren():
                    if source_name == source[2]:
                        return source[3]

    def __enqueue_items(self, ids):
        
        for id in ids:
            self.__shell.add_to_queue(id)
            
    def __get_position(self):

        sp = self.__shell.props.shell_player

        db = self.__shell.props.db

        position = 0
        
        id_now = self.__item_id
        
        if id_now is not None:
            
            if sp.props.playing_from_queue:
                qmodel = self.__queue_sc.props.query_model
            elif self.__playlist_sc is not None:
                qmodel = self.__playlist_sc.get_entry_view().props.model
            else:
                qmodel = None
                
            if qmodel is not None:
                for row in qmodel:
                    id = row[0].get_string(RB.RhythmDBPropType.LOCATION)
                    if id_now == id:
                        break
                    position += 1
                    
        log.debug("position: %i" % position)
        
        return position

