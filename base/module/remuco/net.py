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

import socket
import struct
import time

# disable temporary do not commit
#import bluetooth
from gi.repository import GConf, GObject

from remuco import log
from remuco import message
from remuco import report
from remuco import serial
from remuco.data import ClientInfo
from remuco.remos import zc_publish, zc_unpublish

def build_message(id, serializable):
    """Create a message ready to send on a socket.
    
    @param id:
        message id
    @param serializable:
        message content (object of type Serializable)
    
    @return:
        the message as a binary string or None if serialization failed
        
    """
    
    # This is not included in ClientConnection.send() because if there are
    # multiple clients, each client would serialize the data to send again.
    # Using this method, a message can be serialized once and send to many
    # clients.
    
    if serializable is not None:
        ba = serial.pack(serializable)
        if ba is None:
            log.warning("failed to serialize (msg-id %d)" % id)
            return None
    else:
        ba = b''
    
    header = struct.pack("!hi", id, len(ba))
    
    return header + ba

class ReceiveBuffer(object):
    """ A box to pool some receive buffer related data. """
    
    def __init__(self):
        
        self.header = ""
        self.data = ""
        self.rest = 0
        
class ClientConnection(object):
    
    IO_HEADER_LEN = 6
    IO_MSG_MAX_SIZE = 10240 # prevent DOS
    
    IO_PREFIX = b'\xff\xff\xff\xff'
    IO_SUFFIX = b'\xfe\xfe\xfe\xfe'
    IO_PROTO_VERSION = b'\x0a'
    IO_HELLO = IO_PREFIX + IO_PROTO_VERSION + IO_SUFFIX # hello msg
    
    def __init__(self, sock, addr, clients, pinfo_msg, msg_handler_fn, c_type):
        
        self.__sock = sock
        self.__addr = addr
        self.__clients = clients
        self.__pinfo_msg = pinfo_msg
        self.__msg_handler_fn = msg_handler_fn
        self.__conn_type = c_type
        
        # client info
        self.info = ClientInfo()
        self.__psave = False
        
        # the following fields are used for iterative receiving on message data
        # see io_recv() and io_recv_buff()
        self.__rcv_buff_header = ReceiveBuffer()
        self.__rcv_buff_data = ReceiveBuffer()
        self.__rcv_msg_id = message.IGNORE
        self.__rcv_msg_size = 0
        
        self.__snd_buff = b'' # buffer for outgoing data
        
        # source IDs for various events
        self.__sids = [
            GObject.io_add_watch(self.__sock, GObject.IO_IN, self.__io_recv),
            GObject.io_add_watch(self.__sock, GObject.IO_ERR, self.__io_error),
            GObject.io_add_watch(self.__sock, GObject.IO_HUP, self.__io_hup)
            ]
        self.__sid_out = 0
        
        log.debug("send 'hello' to %s" % self)
        
        self.send(ClientConnection.IO_HELLO)
    
    def __str__(self):
        
        return str(self.__addr)
    
    #==========================================================================
    # io
    #==========================================================================
    
    def __recv_buff(self, rcv_buff):
        """ Receive some data and put it into the given ReceiveBuffer.
        
        @param rcv_buff: the receive buffer to put received data into
        
        @return: true if some data has been received, false if an error occurred
        """
       
        try:
            log.debug("try to receive %d bytes" % rcv_buff.rest)
            data = self.__sock.recv(rcv_buff.rest)
        except socket.timeout as e: # TODO: needed?
            log.warning("connection to %s broken (%s)" % (self, e))
            self.disconnect()
            return False
        except socket.error as e:
            log.warning("connection to %s broken (%s)" % (self, e))
            self.disconnect()
            return False
        
        received = len(data)
        
        log.debug("received %d bytes" % received)
        
        if received == 0:
            log.warning("connection to %s broken (no data)" % self)
            self.disconnect()
            return False
        
        rcv_buff.data = rcv_buff.data + data
        rcv_buff.rest -= received
        
        return True
        
    
    def __io_recv(self, fd, cond):
        """ GObject callback function (when there is data to receive). """
        
        log.debug("data from client %s available" % self)

        # --- init buffers on new message -------------------------------------

        if (self.__rcv_buff_header.rest + self.__rcv_buff_data.rest == 0):

            self.__rcv_msg_id = message.IGNORE
            self.__rcv_msg_size = 0 # will be set later
            
            self.__rcv_buff_header.data = b''
            self.__rcv_buff_header.rest = ClientConnection.IO_HEADER_LEN
            self.__rcv_buff_data.data = b''
            self.__rcv_buff_data.rest = 0 # will be set later
    
        # --- receive header --------------------------------------------------

        if self.__rcv_buff_header.rest > 0:
            
            ok = self.__recv_buff(self.__rcv_buff_header)
            if not ok:
                return False
            if self.__rcv_buff_header.rest > 0:
                return True # more data to read, come back later
            id, size = struct.unpack('!hi', self.__rcv_buff_header.data)
            if size > ClientConnection.IO_MSG_MAX_SIZE:
                log.warning("msg from %s too big (%d bytes)" % (self, size))
                self.disconnect()
                return False
            log.debug("incoming msg: %d, %dB" % (id, size))
            self.__rcv_buff_data.rest = size
            self.__rcv_msg_id, self.__rcv_msg_size = id, size
            if size > 0:
                return True # more data to read, come back later
        
        # --- receive content -------------------------------------------------

        if self.__rcv_buff_data.rest > 0:
            
            ok = self.__recv_buff(self.__rcv_buff_data)
            if not ok:
                return False
            if self.__rcv_buff_data.rest > 0:
                return True # more data to read, come back later
        
        # --- message complete ------------------------------------------------
            
        msg_id = self.__rcv_msg_id
        msg_data = self.__rcv_buff_data.data

        log.debug("incoming msg ")
        
        if msg_id == message.IGNORE:
            
            log.debug("received ignore msg (probably a ping)")
            
        elif msg_id == message.CONN_SLEEP:
            
            self.__psave = True
            
        elif msg_id == message.CONN_WAKEUP:
            
            self.__psave = False
            
            self.__msg_handler_fn(self, message.PRIV_INITIAL_SYNC, None)
            
        elif msg_id == message.CONN_CINFO:
            
            log.debug("received client info from %s" % self)
            
            serial.unpack(self.info, msg_data)
            
            if not self in self.__clients: # initial client info
            
                device = self.info.device.copy()
                device["conn"] = self.__conn_type
                report.log_device(device)
                
                self.__clients.append(self)
                
                log.debug("sending player info to %s" % self)
                
                self.send(self.__pinfo_msg)
                
                self.__msg_handler_fn(self, message.PRIV_INITIAL_SYNC, None)
            
        else:
            
            self.__msg_handler_fn(self, msg_id, msg_data)
        
        return True

    def __io_error(self, fd, cond):
        """ GObject callback function (when there is an error). """
        log.warning("connection to client %s broken" % self)
        self.disconnect()
        return False
        
    def __io_hup(self, fd, cond):
        """ GObject callback function (when other side disconnected). """
        log.info("client %s disconnected" % self)
        self.disconnect()
        return False
    
    def __io_send(self, fd, cond):
        """ GObject callback function (when data can be written). """
        
        if not self.__snd_buff:
            self.__sid_out = 0
            return False

        log.debug("try to send %d bytes to %s" % (len(self.__snd_buff), self))

        try:
            sent = self.__sock.send(self.__snd_buff)
        except socket.error as e:
            log.warning("failed to send data to %s (%s)" % (self, e))
            self.disconnect()
            return False

        log.debug("sent %d bytes" % sent)
        
        if sent == 0:
            log.warning("failed to send data to %s" % self)
            self.disconnect()
            return False
        
        self.__snd_buff = self.__snd_buff[sent:]
        
        if not self.__snd_buff:
            self.__sid_out = 0
            return False
        else:
            return True
    
    def send(self, msg):
        """Send a message to the client.
        
        @param msg:
            complete message (incl. ID and length) in binary format
            (net.build_message() is your friend here)
        
        @see: net.build_message()
        
        """
        
        if msg is None:
            log.error("** BUG ** msg is None")
            return
        
        if self.__sock is None:
            log.debug("cannot send message to %s, already disconnected" % self)
            return

        if self.__psave:
            log.debug("%s is in sleep mode, send nothing" % self)
            return

        self.__snd_buff = self.__snd_buff + msg
        
        # if not already trying to send data ..
        if self.__sid_out == 0:
            # .. do it when it is possible:
            self.__sid_out = GObject.io_add_watch(self.__sock, GObject.IO_OUT,
                                                  self.__io_send)
        
    def disconnect(self, remove_from_list=True, send_bye_msg=False):
        """ Disconnect the client.
        
        @keyword remove_from_list: whether to remove the client from the client
                                   list or not (default is true)
        @keyword send_bye_msg: whether to send a bye message before
                               disconnecting                                       
        """
        
        # send bye message
        
        if send_bye_msg and self.__sock is not None:
            log.info("send 'bye' to %s" % self)
            msg = build_message(message.CONN_BYE, None)
            sent = 0
            retry = 0
            while sent < len(msg) and retry < 10:
                try:
                    sent += self.__sock.send(msg)
                except socket.error as e:
                    log.warning("failed to send 'bye' to %s (%s)" % (self, e))
                    break
                time.sleep(0.02)
                retry += 1
            if sent < len(msg):
                log.warning("failed to send 'bye' to %s" % self)
            else:
                # give client some time to close connection:
                time.sleep(0.1)
        
        # disconnect
        
        log.debug("disconnect %s" % self)
        
        if remove_from_list and self in self.__clients:
            self.__clients.remove(self)
        
        for sid in self.__sids:
            GObject.source_remove(sid)
        
        self.__sids = ()

        if (self.__sid_out > 0):
            GObject.source_remove(self.__sid_out)
            self.__sid_out = 0
        
        if self.__sock is not None:
            try:
                self.__sock.shutdown(socket.SHUT_RDWR)
            except socket.error as e:
                pass
            self.__sock.close()
            self.__sock = None

class _Server(object):
    
    SOCKET_TIMEOUT = 2.5
    
    def __init__(self, clients, pinfo, msg_handler_fn, config):
        """ Create a new server.
        
        @param clients:
            a list to add connected clients to
        @param pinfo:
            player info (type data.PlayerInfo)
        @param msg_handler_fn:
            callback function for passing received messages to
        @param config:
            adapter configuration
                                 
        """
        self.__clients = clients
        self.__msg_handler_fn = msg_handler_fn
        self.__pinfo_msg = build_message(message.CONN_PINFO, pinfo)
        self.__sid = None
        
        self._pinfo = pinfo
        self._config = config
        self._sock = None
        
        # set up socket
        
        try:
            self._sock = self._create_socket()
            self._sock.settimeout(_Server.SOCKET_TIMEOUT)
        except (IOError, socket.error) as e:
            # TODO: socket.error may be removed when 2.5 support is dropped
            log.error("failed to set up %s server (%s)" % (self._get_type(), e))
            return
        
        log.info("created %s server" % self._get_type())
        
        # watch socket
        
        self.__sid = GObject.io_add_watch(self._sock,
            GObject.IO_IN | GObject.IO_ERR | GObject.IO_HUP, self.__handle_io)
        
    #==========================================================================
    # io
    #==========================================================================

    def __handle_io(self, fd, condition):
        """ GObject callback function (when there is a socket event). """
        
        if condition == GObject.IO_IN:
            
            try:
                log.debug("connection request from %s client" % self._get_type())
                client_sock, addr = self._sock.accept()
                log.debug("connection request accepted")
                client_sock.setblocking(0)
                ClientConnection(client_sock, addr, self.__clients,
                                 self.__pinfo_msg, self.__msg_handler_fn,
                                 self._get_type())
            except IOError as e:
                log.error("accepting %s client failed: %s" %
                          (self._get_type(), e))
            
            return True
        
        else:
            
            log.error("%s server socket broken" % self._get_type())
            self.__sid = None
            return False
    
    def down(self):
        """ Shut down the server. """
        
        if self.__sid is not None:
            GObject.source_remove(self.__sid) 

        if self._sock is not None:
            log.debug("closing %s server socket" % self._get_type())
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except socket.error:
                pass
            self._sock.close()
            self._sock = None

    def _create_socket(self):
        """ Create the server socket.
        
        @return: a socket object
        
        """
        raise NotImplementedError
    
    #==========================================================================
    # miscellaneous
    #==========================================================================

    def _get_type(self):
        """Get server type name."""
        raise NotImplementedError
    
#class BluetoothServer(_Server):
#    
#    UUID = "025fe2ae-0762-4bed-90f2-d8d778f020fe"
#
#    def _create_socket(self):
#        
#        try:
#            sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
#            sock.bind(("", self._config.bluetooth_channel or bluetooth.PORT_ANY))
#            sock.listen(1)
#            sock.settimeout(0.33)
#            bluetooth.advertise_service(sock, self._pinfo.name,
#                service_id=BluetoothServer.UUID,
#                service_classes=[BluetoothServer.UUID, bluetooth.SERIAL_PORT_CLASS],
#                profiles=[bluetooth.SERIAL_PORT_PROFILE])
#        except Exception as e:
#            # bluez does not always convert its internal error into a
#            # IOError-based BluetoothError, so we need to catch here everything
#            # and convert internal Bluetooth errors to regular IO errors.
#            if isinstance(e, IOError):
#                raise e
#            else:
#                raise IOError(*e)
#        
#        return sock
#        
#    def down(self):
#        
#        if self._sock is not None:
#            try:
#                bluetooth.stop_advertising(self._sock)
#            except bluetooth.BluetoothError as e:
#                log.warning("failed to unregister bluetooth service (%s)" % e)
#        
#        super(BluetoothServer, self).down()
#        
#    def _get_type(self):
#        return "bluetooth"
                
class WifiServer(_Server):
    
    def _create_socket(self):
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('', self._config.wifi_port))
        sock.listen(1)
        
        zc_publish(self._pinfo.name, sock.getsockname()[1])
        
        return sock

    def _get_type(self):
        return "wifi"

    def down(self):
    
        zc_unpublish()
        super(WifiServer, self).down()

