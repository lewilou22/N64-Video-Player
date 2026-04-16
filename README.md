# N64-Video-Player
N64 Video Player For Flash Carts

Use the Provided SDVideo.z64 ROM to play videos from your N64 flash cart SD card (Need to convert to N64 format see below for conversion instructions)

Download all files and run GUI python script of choice 

The GUIs can be used with out a libdragon install for video and audio conversion

If building a packed ROM read the Libdragon instructions below

Put all files on your Everdrives SD card

PLAY MOVIES ON N64 !  


https://www.youtube.com/watch?v=ddS_CBplqi0



Windows Libdragon Easy install script for everything needed to make a ROM to play a video with sound on the N64.

I have included the code now. This uses Libdragon

For Windows or Linux:

Install Ubuntu 24 or later
https://learn.microsoft.com/en-us/windows/wsl/install

Unzip the project folder on your WSL2 enviroment

cd N64-Libdragon-WSL2/scripts
bash wsl_bootstrap_libdragon.sh

close the WSL2 terminal

open new WSL2 terminal

cd N64-Libdragon-WSL2/scripts
python3 video2n64_gui.py

Point N64_INST to the new libdragon-preview folder (one folder before N64-Libdragon-WSL2)

Repo ROOT is N64-Libdragon-WSL2 folder

Make a Movie ROM . Play on N64. Enjoy it
