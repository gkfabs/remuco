/*   
 *   Remuco - A remote control system for media players.
 *   Copyright (C) 2006-2010 by the Remuco team, see AUTHORS.
 *
 *   This file is part of Remuco.
 *
 *   Remuco is free software: you can redistribute it and/or modify
 *   it under the terms of the GNU General Public License as published by
 *   the Free Software Foundation, either version 3 of the License, or
 *   (at your option) any later version.
 *
 *   Remuco is distributed in the hope that it will be useful,
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *   GNU General Public License for more details.
 *
 *   You should have received a copy of the GNU General Public License
 *   along with Remuco.  If not, see <http://www.gnu.org/licenses/>.
 *   
 */

package remuco.client.android;

import remuco.client.common.data.Item;
import remuco.client.common.data.PlayerInfo;
import remuco.client.common.data.Progress;
import remuco.client.common.data.State;
import remuco.client.common.player.Player;
import remuco.client.common.util.Log;
import android.graphics.BitmapFactory;
import android.os.Handler;
import android.os.Message;
import android.widget.Toast;

public class ViewHandler extends Handler {

	int tick;
	
	byte[] imageCache;
	
	Remuco remuco;
	
	boolean running = false;
	
	
	public ViewHandler(Remuco remuco) {
		this.remuco = remuco;
		this.postDelayed(new Ticker(), 1000);
	}

    public void setRunning(boolean r) {
        updateItemGui(remuco.getPlayer().getPlayer().item);
        updateProgressGui(remuco.getPlayer().getPlayer().progress);
        updateStateGui(remuco.getPlayer().getPlayer().state);

        running = r;
    }

	@Override
	public void handleMessage(Message msg) {

		switch(msg.what){
		
		case MessageFlag.CONNECTED:
			
			Log.ln("[VH] CONNECTED!");
			
			// obj should be of type PlayerInfo
			PlayerInfo info = (PlayerInfo)msg.obj;
			
			// inform the user
			String toast = remuco.getResources().getString(
					R.string.connection_successful, 
					info.getName()
					);
			Toast.makeText(remuco, toast, Toast.LENGTH_SHORT).show();
			
			// enable buttons
			remuco.ctrlPrev.setClickable(true);
			remuco.ctrlPlay.setClickable(true);
			remuco.ctrlNext.setClickable(true);
			remuco.ctrlShuffle.setClickable(true);
			remuco.ctrlRepeat.setClickable(true);
			
			// update rating bar
			remuco.infoRatingBar.setNumStars(info.getMaxRating());
			
			// update the display
			sendEmptyMessage(MessageFlag.ITEM_CHANGED);

			break;
			
		case MessageFlag.DISCONNECTED:
			
			Log.ln("[VH] DISCONNECTED!");
			
			// inform the user
			toast = remuco.getResources().getString(R.string.connection_failed);
			Toast.makeText(remuco, toast, Toast.LENGTH_SHORT).show();
			
			// disable buttons
			remuco.ctrlPrev.setClickable(false);
			remuco.ctrlPlay.setClickable(false);
			remuco.ctrlNext.setClickable(false);
			remuco.ctrlShuffle.setClickable(false);
			remuco.ctrlRepeat.setClickable(false);

			// show ape picture
			remuco.infoCover.setImageResource(R.drawable.remuco_128);
			
			// change text
			remuco.infoTitle.setText("not connected");
			remuco.infoArtist.setText("use the menu to connect");
			remuco.infoAlbum.setText("to your remuco server");
			
			// remove stars
			remuco.infoRatingBar.setProgress(0);
			
			// set times
			remuco.ctrlLength.setText("--:--");
			remuco.ctrlProgress.setText("--:--");
			
			// not running anymore
			running = false;
			
			break;
			
		case MessageFlag.ITEM_CHANGED:
			
			Log.ln("[VH] item changed");

			// msg.obj should be of type Item
			if(!(msg.obj instanceof Item)){
//				Log.bug("[VH] msg.obj should be of type Item");
//				Log.bug("[VH] msg.obj: " + msg.obj);
				break;
			}
			Item item = (Item)msg.obj;
            updateItemGui(item);
			
			// done
			break;


		case MessageFlag.PROGRESS_CHANGED:
			
			Log.ln("[VH] progress changed");
			
			// msg.obj should be of type Progress
			Progress progress = (Progress)msg.obj;
			updateProgressGui(progress);
			
			break;
			
			
		case MessageFlag.TICK:
			
			/*
			 * the tick message ... tick is send every second
			 * this is used to update the progress bar every second
			 * although the player sends its updates only every fifth second
			 */
			
			// don't update if the player is not running
//			if(player == null || player.state.getPlayback() != State.PLAYBACK_PLAY) break;
			
			tick++;
			
			remuco.ctrlProgressBar.setProgress(tick);
			
			remuco.ctrlProgress.setText(
					String.format("%02d:%02d", 
							tick/60,
							tick%60)
					);
			
			break;
			
			
		case MessageFlag.STATE_CHANGED:
			
			Log.ln("[VH] state changed");
			
			// msg.obj should be of type State
			State state = (State)msg.obj;

            updateStateGui(state);
			
			break;
			
		}


	}

	// --- convert byte[] to image
	private void convertByteArrayToImage(byte[] image) {
		if(image == null || image.length == 0){
			remuco.infoCover.setImageResource(R.drawable.remuco_128);

            imageCache = null;
		} else {
			remuco.infoCover.setImageBitmap(
					BitmapFactory.decodeByteArray(image, 0, image.length)
			);
			
			imageCache = image;
		}
	}

    private void updateItemGui(Item item) {
        // get song metadata
        String title = item.getMeta(Item.META_TITLE);
        String artist = item.getMeta(Item.META_ARTIST);
        String album = item.getMeta(Item.META_ALBUM);
			
        Log.ln(String.format("now playing: \"%s\" by \"%s\" on \"%s\"",
                             title, artist, album));
			
        // set song metadata
        remuco.infoTitle.setText(title);
        remuco.infoArtist.setText(artist);
        remuco.infoAlbum.setText(album);

        // set image
        byte[] image = item.getImg();
        convertByteArrayToImage(image);
			
        // update rating bar
        remuco.infoRatingBar.setProgress(item.getRating());
    }

    private void updateProgressGui(Progress progress) {
        // set progress
        // the actual progress is set every second in the ticker runnable
        remuco.ctrlProgressBar.setMax(progress.getLength());
        remuco.ctrlLength.setText(String.format("%02d:%02d", 
					progress.getLength()/60,
					progress.getLength()%60));
			
        // set tick
        tick = progress.getProgress();
    }	

    private void updateStateGui(State state) {
			// toggle playbutton icon
			if(state.getPlayback() == State.PLAYBACK_PLAY){
				Log.debug("[VH] playback = true");
				remuco.ctrlPlay.setImageResource(R.drawable.button_pause);
				running = true;
			} else {
				Log.debug("[VH] playback = false");
				remuco.ctrlPlay.setImageResource(R.drawable.button_play);
				running = false;
			}
			
			// toggle shuffle icon
			if(state.isShuffle()){
				Log.debug("[VH] shuffle = true");
				remuco.ctrlShuffle.setImageResource(R.drawable.button_shuffle);
			} else {
				Log.debug("[VH] shuffle = false");
				remuco.ctrlShuffle.setImageResource(R.drawable.button_noshuffle);
			}
			
			// toggle repeat icon
			if(state.isRepeat()){
				Log.debug("[VH] repeat = true");
				remuco.ctrlRepeat.setImageResource(R.drawable.button_repeat);
			} else {
				Log.debug("[VH] repeat = false");
				remuco.ctrlRepeat.setImageResource(R.drawable.button_norepeat);
			}
			
//			// set volume
//			VolumeDialog vDialog = remuco.getVolumeDialog();
//			
//			if(vDialog!=null)
//				vDialog.setVolume(player.state.getVolume());
    }

	class Ticker implements Runnable {
		
		@Override
		public void run() {
			if(running){
				
				// increase tick time
				tick++;
				
				// set progress text
				remuco.ctrlProgress.setText(String.format("%02d:%02d", 
							tick/60,
							tick%60));
				
				// set progress bar
				remuco.ctrlProgressBar.setProgress(tick);
			}
			
			// repost us
			postDelayed(this, 1000);
		}
	}


}
