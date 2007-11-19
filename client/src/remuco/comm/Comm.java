package remuco.comm;

import remuco.Remuco;
import remuco.UserException;
import remuco.player.StringParam;
import remuco.util.Log;
import remuco.util.Tools;

/**
 * The communication layer. To be used by the player to send and receive
 * {@link Message}s.
 * 
 * @author Christian Buennig
 * 
 */
public final class Comm extends Thread implements IMessageSender {

	private static final int CONN_RETRY_IVAL = 10;

	private final DeviceConnector dc;

	private final EmulatedServer es;

	private boolean interrupted;

	/**
	 * Used to signal local events (e.g. errors) to the used
	 * {@link IMessageReceiver}.
	 */
	private final Message localMsg = new Message();

	/**
	 * Data used for {@link #localMsg}.
	 */
	private final StringParam localMsgSP = new StringParam();

	private final IMessageReceiver mr;

	private Net net;

	private final Serializer srz = new Serializer();

	/**
	 * Creates a new communication layer for the given device and the given
	 * player. The communication layer runs in a new thread and hands out
	 * received messages to <code>client</code>. This may inlcude locally
	 * generated messages (e.g. {@link Message#ID_LOCAL_CONNECTED}) to inform
	 * about state changes of the communication layer.
	 * 
	 * @param device
	 *            the device to communicate with
	 * @param client
	 *            handler for the received messages
	 */
	public Comm(String device, IMessageReceiver mr) throws UserException {

		dc = new DeviceConnector(device);

		if (Remuco.EMULATION) {
			es = new EmulatedServer(mr);
		} else {
			es = null;
		}

		this.mr = mr;

		this.start();

	}

	/**
	 * Shuts down the communication layer. The communication layer's thread gets
	 * interrupted.
	 * 
	 */
	public void down() {

		interrupted = true;

		if (Remuco.EMULATION) {

			es.stop();

			return;

		}

		this.interrupt();

		if (net != null)
			net.down();

		net = null;

	}

	public void run() {

		Log.ln("[CM] starting");

		net = null;

		if (Remuco.EMULATION) {

			Tools.sleep(1000);

			localMsg.id = Message.ID_LOCAL_CONNECTED;
			localMsg.sd = null;

			mr.receiveMessage(localMsg);

			es.loop();

		}

		while (!interrupted) {

			net = connect();

			if (net == null || interrupted) {
				break;
			}

			localMsg.id = Message.ID_LOCAL_CONNECTED;
			localMsg.sd = null;

			mr.receiveMessage(localMsg);

			receiveMessages();

			if (!interrupted) {
				localMsgSP.setParam("Lost connection.\nReconnect..");
				localMsg.id = Message.ID_LOCAL_DISCONNECTED;
				localMsg.sd = localMsgSP.sdGet();
				mr.receiveMessage(localMsg);

				Tools.sleep(2000);
			}
		}

		Log.ln("[CM] stopped");

	}

	public void sendMessage(Message m) {

		Log.asssert(this, m.bd == null);

		if (Remuco.EMULATION) {

			es.sendMessage(m);

			return;

		}

		if (net == null || !net.isUp()) {
			return;
		}

		srz.sd2bd(m);

		Log.asssert(this, m.bd);

		if (net != null && net.isUp() && net.send(m) < 0) {

			net = null;

		}

	}

	/**
	 * Tries continuously to set up the net connection until success or a
	 * serious error occurred.
	 * 
	 * @return the net connection or <code>null</code> if a serious error
	 *         occurred and proceeding does not make much sense
	 * 
	 */
	private Net connect() {

		Net net = null;

		int n = 0;

		while (!interrupted) {

			n++;

			try {
				dc.createNetConnection();

				net = dc.getNetConnection();

			} catch (UserException e) {

				Log.ln("[CM] ", e);

				localMsgSP.setParam(e.getError() + "\n" + e.getDetails());
				localMsg.id = Message.ID_LOCAL_ERROR;
				localMsg.sd = localMsgSP.sdGet();
				mr.receiveMessage(localMsg);

				return null;

			}

			if (net == null) {

				localMsgSP.setParam("Connecting failed\n(" + n + " times)\n"
						+ "Retry ..");
				localMsg.id = Message.ID_LOCAL_DISCONNECTED;
				localMsg.sd = localMsgSP.sdGet();
				mr.receiveMessage(localMsg);

				Tools.sleep(CONN_RETRY_IVAL * 1000);

			} else {

				break;

			}

		}

		return net;

	}

	/**
	 * Continuesly receives messages and delivers them (unserialized) to the
	 * player. The method returns either if the net connection is broken or if
	 * the flag {@link #interrupted} is true.
	 * 
	 * @param net
	 *            the net connection
	 */
	private void receiveMessages() {

		Log.asssert(this, net != null);

		if (!net.isUp()) { // yes, this may happen
			net.down();
			net = null;
			return;
		}

		Message m = new Message();

		while (!interrupted) {

			if (net.recv(m) < 0) {
				net = null;
				return;
			}

			if (m.bd != null) {

				try {
					srz.bd2sd(m);
				} catch (BinaryDataExecption e) {
					Log.ln("[CM] rxed dataObj is malformed (" + e.getMessage()
							+ ")");
					m.bd = null;
					continue;
				}
			}

			if (m.id == Message.ID_IFS_SRVDOWN) {
				net.down();
				net = null;
				return;
			}

			mr.receiveMessage(m);

			m.sd = null;

		}

	}

}
