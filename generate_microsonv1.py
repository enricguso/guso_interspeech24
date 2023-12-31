import masp as srs
import numpy as np
import soundfile as sf
from IPython.display import Audio
import scipy.signal as sig
import copy
import pandas as pd
import os
import json
from os.path import join as pjoin
from multiprocessing import Pool
import matplotlib.pyplot as plt
import mat73
import pyrubberband as pyrb
from multiprocessing import Pool
import helpers as hlp
import importlib
importlib.reload(hlp);
import argparse

parser = argparse.ArgumentParser(
        description='Dataset Generation Argument Parser')
parser.add_argument("--mls_path", type=str,
                help="""Folder containing the MultiLingual LibriSpeech Dataset.""",
                default=None)
parser.add_argument("--wham_path", type=str,
                help="""Folder containing the WHAM! Dataset.""",
                default=None)
parser.add_argument("--output", type=str,
                help="""Directory where to save processed wavs.""",
                default=None)
args = parser.parse_args()

# This script encapsulated in a method for multi-processing takes a dataframe row and stores the audio on disk
# a = df.iloc[i]
def process(a):
    try:
        # take the head center coordinates from the dataframe and compute the ear positions. set mic coordinates there.
        mic = np.array(hlp.head_2_ku_ears(np.array([a.headC_x, a.headC_y, a.headC_z]),
                                            np.array([a.headOrient_azi,a.headOrient_ele])))
        ##############################
        # NOISE PROCESSING (NO SIMULATION, ONLY AUGMENTATION):
        # load noise:
        noise, _ = sf.read(pjoin(pjoin(pjoin(wham_path, 'wham_noise'), a.wham_split), a.noise_path))

        # time stretch if needed
        if a.stretch != 0.0:
            noise = pyrb.time_stretch(noise, a.fs_noise, a.stretch)

        # extend if needed with hanning window
        noise = np.array([hlp.extend_noise(noise[:,0], a.num_chunks * 4 * a.fs_noise, a.fs_noise),
                hlp.extend_noise(noise[:,1], a.num_chunks * 4 * a.fs_noise, a.fs_noise)]).T
        # crop 4 seconds chunk
        noise = noise[a.chunk * 4 * a.fs_noise :(a.chunk + 1) * 4 * a.fs_noise]

        # invert phase for augmentation
        if a.phase_inv:
            noise *= -1

        # invert channels for augmentation
        if a.lr_inv:
            noise = noise[:, [1,0]]

        noise = noise.T
        ###############################
        # load speech and crop at the 4s chunk that has more energy
        speech_folder = pjoin(pjoin(mls_path, a.mls_split), 'audio')
        speech, _ = sf.read(pjoin(pjoin(pjoin(speech_folder, str(a.speaker)), str(a.book)), a.speech_path))
        env = sig.fftconvolve(speech, np.ones(4*a.fs_noise), 'same')
        idx_candidates = np.flip(np.argsort(env**2))
        idx = idx_candidates[idx_candidates < (len(speech)-(4*a.fs_noise))][0]
        speech = speech[idx:idx+4*a.fs_noise]

        # IR simulation
        room = np.array([a.room_x, a.room_y, a.room_z])
        rt60 = np.array([a.rt60])
        rt60 *= 0.5 #furniture absorption 
        #snr 0, more people, more reduction -> 0.3 * rt60
        #snr 5, less people, no rt60 reduction -> 1.0 * rt60
        rt60 *= ((a.snr+0.3)/5.3) # people absoprtion
        src = np.array([[a.src_x, a.src_y, a.src_z]])
        head_orient = np.array([a.headOrient_azi, a.headOrient_ele])

        # Compute absorption coefficients for desired rt60 and room dimensions
        abs_walls,rt60_true = srs.find_abs_coeffs_from_rt(room, rt60)
        # Small correction for sound absorption coefficients:
        if sum(rt60_true-rt60>0.05*rt60_true)>0 :
            abs_walls,rt60_true = srs.find_abs_coeffs_from_rt(room, rt60_true + abs(rt60-rt60_true))

        # Generally, we simulate up to RT60:
        limits = np.minimum(rt60, maxlim)
        # Compute IRs with MASP at 48k:
        abs_echograms = srs.compute_echograms_sh(room, src, mic, abs_walls, limits, ambi_order, head_orient)
        ane_echograms = hlp.crop_echogram(copy.deepcopy(abs_echograms))
        mic_rirs = srs.render_rirs_sh(abs_echograms, band_centerfreqs, fs_rir)/np.sqrt(4*np.pi)
        ane_rirs = srs.render_rirs_sh(ane_echograms, band_centerfreqs, fs_rir)/np.sqrt(4*np.pi)
        # Pad anechoic rirs so we don't loose alignment when convolving
        zeros_to_pad = len(mic_rirs) - len(ane_rirs)
        zeros_to_pad = np.zeros((zeros_to_pad, mic_rirs.shape[1], mic_rirs.shape[2], mic_rirs.shape[3]))
        ane_rirs = np.concatenate((ane_rirs, zeros_to_pad))
        # Decode SH IRs to binaural
        bin_ir = np.array([sig.fftconvolve(np.squeeze(mic_rirs[:,:,0, 0]), decoder[:,:,0], 'full', 0).sum(1),
                            sig.fftconvolve(np.squeeze(mic_rirs[:,:,1, 0]), decoder[:,:,1], 'full', 0).sum(1)])
        bin_aneIR = np.array([sig.fftconvolve(np.squeeze(ane_rirs[:,:,0, 0]), decoder[:,:,0], 'full', 0).sum(1),
                            sig.fftconvolve(np.squeeze(ane_rirs[:,:,1, 0]), decoder[:,:,1], 'full', 0).sum(1)])
        
        # Apply to the source signal
        reverberant_src = np.array([sig.fftconvolve(sig.resample_poly(speech,fs_rir,fs_target), bin_ir[0, :], 'same'), sig.fftconvolve(sig.resample_poly(speech,fs_rir,fs_target), bin_ir[1, :], 'same')])
        anechoic_src = np.array([sig.fftconvolve(sig.resample_poly(speech,fs_rir,fs_target), bin_aneIR[0, :], 'same'), sig.fftconvolve(sig.resample_poly(speech,fs_rir,fs_target), bin_aneIR[1, :], 'same')])
        
        # Downsample to 16k:
        reverberant_src = np.array([sig.resample_poly(reverberant_src[0], fs_target, fs_rir), 
                            sig.resample_poly(reverberant_src[1], fs_target, fs_rir)])
        anechoic_src = np.array([sig.resample_poly(anechoic_src[0], fs_target, fs_rir), 
                            sig.resample_poly(anechoic_src[1], fs_target, fs_rir)])
        # Apply RIC correction bell filter at 2kHz resonance:
        reverberant_src = np.array([sig.lfilter(filt_b, filt_a, reverberant_src[0]), sig.lfilter(filt_b, filt_a, reverberant_src[1])])

        anechoic_src = np.array([sig.lfilter(filt_b, filt_a, anechoic_src[0]), sig.lfilter(filt_b, filt_a, anechoic_src[1])])

        # Apply SNR:
        ini_snr = 10 * np.log10(hlp.power(reverberant_src) / hlp.power(noise) + np.finfo(noise.dtype).resolution)
        noise_gain_db = ini_snr - a.snr

        noise = noise * np.power(10, noise_gain_db/20)

        # Amplitude normalization:
        norm_fact = np.max(np.abs(reverberant_src + noise))
        anechoic_src /= norm_fact
        noise /= norm_fact
        reverberant_src /= norm_fact

        anechoic_src *= 0.99
        noise *= 0.99
        reverberant_src *= 0.99
        
        writepath = pjoin(output_path, a.mls_split)
        sf.write(pjoin(pjoin(writepath, 'anechoic'), os.path.splitext(a.speech_path)[0]+'.wav'), anechoic_src.T, fs_target, subtype='FLOAT')
        sf.write(pjoin(pjoin(writepath, 'reverberant'), os.path.splitext(a.speech_path)[0]+'.wav'), reverberant_src.T, fs_target, subtype='FLOAT')
        sf.write(pjoin(pjoin(writepath, 'noise'), os.path.splitext(a.speech_path)[0]+'.wav'), noise.T, fs_target, subtype='FLOAT')
        sf.write(pjoin(pjoin(writepath, 'ir'), os.path.splitext(a.speech_path)[0]+'.wav'), bin_ir.T, fs_target, subtype='FLOAT')
        sf.write(pjoin(pjoin(writepath, 'ane_ir'), os.path.splitext(a.speech_path)[0]+'.wav'), bin_aneIR.T, fs_target, subtype='FLOAT')
        # Add mono IRs for other works:
        sf.write(pjoin(pjoin(writepath, 'mono_ir'), os.path.splitext(a.speech_path)[0]+'.wav'), mic_rirs[:, 0, 0, 0], fs_target, subtype='FLOAT')
        print('Processed ' + str(a.idx))
    except:
        print('ERROR when processing ' + str(a.idx))

if __name__ == '__main__':
    print('RIC-corrected dataset generation script. Microson_v1')
    num_workers = 8 # number of CPU cores

    #   decoder_path = 'ku100_inear_test.mat'
    decoder_path = pjoin('decoders_ord10', 'RIC_Front_Omni_ALFE_Window_SinEQ_bimag.mat')

    mls_path = args.mls_path #'/home/ubuntu/Data/mls_spanish'
    wham_path = args.wham_path #'/home/ubuntu/Data/wham'
    output_path = args.output_path #'/home/ubuntu/Data/microson_v1/'

    df_path = 'meta_microson_v1.csv'
    df = pd.read_csv(df_path)
    fs_rir = 48000
    fs_target = 16000 
    ambi_order = 10
    maxlim = 2. # Maximum reverberation time
    band_centerfreqs=np.array([1000]) #change this for multiband #The highest center frequency must be at most equal to fs/2, in order to avoid aliasing.
    #The lowest center frequency must be at least equal to 30 Hz.
    # load BiMagLS decoder from two sound fiels to HA binaural signals:
    decoder = mat73.loadmat(decoder_path)['hnm']
    decoder = np.roll(decoder,500,axis=0)

    # make dirs
    sets = ['train', 'dev', 'test']
    if not os.path.exists(output_path):
        os.makedirs(output_path)
    for subset in sets:
        if not os.path.exists(pjoin(output_path, subset)):
            os.makedirs(pjoin(output_path, subset))
        if not os.path.exists(pjoin(pjoin(output_path, subset), 'anechoic')):
            os.makedirs(pjoin(pjoin(output_path, subset), 'anechoic'))
        if not os.path.exists(pjoin(pjoin(output_path, subset), 'reverberant')):
            os.makedirs(pjoin(pjoin(output_path, subset), 'reverberant'))
        if not os.path.exists(pjoin(pjoin(output_path, subset), 'noise')):
            os.makedirs(pjoin(pjoin(output_path, subset), 'noise'))
        if not os.path.exists(pjoin(pjoin(output_path, subset), 'ir')):
            os.makedirs(pjoin(pjoin(output_path, subset), 'ir'))
        if not os.path.exists(pjoin(pjoin(output_path, subset), 'ane_ir')):
            os.makedirs(pjoin(pjoin(output_path, subset), 'ane_ir'))
        if not os.path.exists(pjoin(pjoin(output_path, subset), 'mono_ir')):
            os.makedirs(pjoin(pjoin(output_path, subset), 'mono_ir'))  
     # also save the configuration:
    config = {'mls_path' : mls_path, 'wham_path' : wham_path, 
              'decoder_path' : decoder_path, 'df_path' : df_path,
              'fs_rir' : fs_rir, 'fs_target' : fs_target, 'ambi_order': ambi_order, 'success': False}
    with open(pjoin(output_path, 'config.json'), 'w') as f:
        json.dump(config, f)
    
    # Correction filter for the RIC resonance of KU100_HA HRIRs
    filt_b, filt_a = hlp.bell(2300, fs_target, np.power(10, -18/20), 8.)


    with Pool(num_workers) as p:
        p.map(process, [df.iloc[i] for i in range(len(df))])
    
    print('Multiprocessing files processed. Starting single-thread process:')
    # Some files can't be processed by multiprocessing (I guess it's the time-stretching library:
    processed_files = os.listdir(pjoin(pjoin(output_path, 'train'), 'reverberant'))
    processed_files = [os.path.splitext(x)[0]+'.flac' for x in processed_files]
    unprocessed_files = df[~df['speech_path'].isin(processed_files)]
    for i in range(len(unprocessed_files)):
        process(unprocessed_files.iloc[i])
        
    config['success'] = True
    with open(pjoin(output_path, 'config.json'), 'w') as f:
        json.dump(config, f)
    print('All files processed. Done.')
