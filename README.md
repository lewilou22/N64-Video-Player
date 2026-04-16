# N64-Video-Player
N64 Video Player For Flash Carts

Use the Provided SDVideo.z64 ROM to play videos from your N64 flash cart SD card (Need to convert to N64 format see below for conversion instructions)

The GUIs can be used with out a libdragon install for video and audio conversion OR with libdragon for ROM packing 

OPTIONAL: If building a packed ROM read the Libdragon instructions below

https://www.youtube.com/watch?v=ddS_CBplqi0

Non libdragon Setup:
Add FFmpeg to your system path.

DOWNLOAD HERE https://www.gyan.dev/ffmpeg/builds/ffmpeg-git-essentials.7z

Unzip audioconv64 for the Audio GUI script https://github.com/lewilou22/N64-Video-Player/releases/download/V1.02/audioconv64.7z

Download all files and run GUI python scripts Video2n64 then audio2n64

For .wav64, use audio2n64  GUI with your converted .wav file after video conversion 

You now have 2 files, .M1V and .wav64

Put all files on your Everdrives SD card

PLAY MOVIES ON N64 ! 



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
