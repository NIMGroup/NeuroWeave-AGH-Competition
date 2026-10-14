"""Reviewed EEG/MEG operations. All products are written to the supplied run directory."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import mne
import numpy as np


def load(path):
    suffix=path.suffix.lower()
    readers={'.fif':mne.io.read_raw_fif,'.edf':mne.io.read_raw_edf,
             '.bdf':mne.io.read_raw_bdf,'.set':mne.io.read_raw_eeglab}
    if suffix not in readers:
        raise ValueError('Supported raw formats: FIF, EDF, BDF, SET')
    return readers[suffix](path,preload=True,verbose='ERROR')


def epoch(raw,args):
    if args.events:
        events=mne.read_events(args.events)
        return mne.Epochs(raw,events,tmin=args.tmin,tmax=args.tmax,
                          baseline=(None,0) if args.tmin<0 else None,preload=True,verbose='ERROR')
    return mne.make_fixed_length_epochs(raw,duration=args.epoch_seconds,preload=True,verbose='ERROR')


def filtering(raw,args):
    if not 0<=args.high_pass<args.low_pass<raw.info['sfreq']/2:
        raise ValueError('Filter cutoffs must satisfy 0 <= high_pass < low_pass < Nyquist')
    raw=raw.copy().filter(args.high_pass or None,args.low_pass,method='iir',verbose='ERROR')
    if args.notch and args.notch<raw.info['sfreq']/2:
        raw.notch_filter([args.notch],method='iir',verbose='ERROR')
    if args.sfreq and args.sfreq<raw.info['sfreq']:
        if args.low_pass>=args.sfreq/2:
            raise ValueError('Requested resampling rate is too low for the selected passband')
        raw.resample(args.sfreq,verbose='ERROR')
    return raw


def clean_ica(raw,args,out):
    from mne.preprocessing import ICA
    picks=mne.pick_types(raw.info,eeg=True,meg=True,exclude='bads')
    if len(picks)<3: raise ValueError('ICA requires at least three good data channels')
    ica=ICA(n_components=min(args.components,len(picks)-1),random_state=args.seed,max_iter=500)
    ica.fit(raw.copy().filter(1,None,method='iir',verbose='ERROR'),picks=picks,verbose='ERROR')
    exclude=[]
    if mne.pick_types(raw.info,eog=True).size:
        exclude.extend(ica.find_bads_eog(raw,verbose='ERROR')[0])
    if mne.pick_types(raw.info,ecg=True).size:
        exclude.extend(ica.find_bads_ecg(raw,verbose='ERROR')[0])
    if args.exclude_components:
        exclude.extend(int(x) for x in args.exclude_components.split(','))
    if any(x<0 or x>=ica.n_components_ for x in exclude): raise ValueError('ICA component index out of range')
    ica.exclude=sorted(set(exclude))
    ica.save(out/'decomposition-ica.fif',overwrite=True)
    (out/'ica_selection.json').write_text(json.dumps({'excluded':ica.exclude,'automatic_reference_channels':bool(mne.pick_types(raw.info,eog=True,ecg=True).size),
        'note':'No component is removed without an EOG/ECG match or explicit component indices.'},indent=2))
    return ica.apply(raw.copy(),verbose='ERROR')


def connectivity(epochs,args,out):
    from mne_connectivity import spectral_connectivity_epochs
    con=spectral_connectivity_epochs(epochs,method=['plv','coh','wpli'],mode='multitaper',
        sfreq=epochs.info['sfreq'],fmin=args.freq_min,fmax=args.freq_max,faverage=True,verbose='ERROR')
    np.savez_compressed(out/'connectivity.npz',**{name:item.get_data(output='dense') for name,item in zip(['plv','coh','wpli'],con)},channels=np.array(epochs.ch_names,dtype='U'))


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--operation',choices=['inspect','bad-channels','filter','ica','epoch','autoreject','connectivity','time-frequency','maxwell','source'],required=True)
    parser.add_argument('--events',type=Path)
    parser.add_argument('--forward',type=Path)
    parser.add_argument('--noise-cov',type=Path)
    parser.add_argument('--high-pass',type=float,default=.5)
    parser.add_argument('--low-pass',type=float,default=40)
    parser.add_argument('--notch',type=float,default=0)
    parser.add_argument('--sfreq',type=float,default=0)
    parser.add_argument('--epoch-seconds',type=float,default=2)
    parser.add_argument('--tmin',type=float,default=-.2)
    parser.add_argument('--tmax',type=float,default=.8)
    parser.add_argument('--components',type=int,default=10)
    parser.add_argument('--exclude-components',default='')
    parser.add_argument('--seed',type=int,default=123)
    parser.add_argument('--freq-min',type=float,default=8)
    parser.add_argument('--freq-max',type=float,default=13)
    parser.add_argument('--lof-threshold',type=float,default=1.5)
    args=parser.parse_args(argv)
    out=args.output_dir; out.mkdir(parents=True,exist_ok=True)
    raw=load(args.input); op=args.operation
    if not np.isfinite(raw.get_data()).all(): raise ValueError('Raw data contains non-finite values')
    summary={'operation':op,'channels':len(raw.ch_names),'samples':int(raw.n_times),'sfreq':float(raw.info['sfreq'])}
    if op=='bad-channels':
        from mne.preprocessing import find_bad_channels_lof
        if not mne.pick_types(raw.info,eeg=True).size: raise ValueError('LOF interpolation requires EEG channels with positions')
        if not raw.get_montage(): raw.set_montage('standard_1020',on_missing='raise')
        bads=find_bad_channels_lof(raw,n_neighbors=min(10,len(raw.ch_names)-1),threshold=args.lof_threshold,picks='eeg')
        raw.info['bads']=sorted(set(raw.info['bads']+bads)); summary['detected_bads']=bads
        if bads: raw.interpolate_bads(reset_bads=True)
    if op=='filter': raw=filtering(raw,args)
    if op=='ica': raw=clean_ica(raw,args,out)
    if op=='maxwell':
        if not mne.pick_types(raw.info,meg=True).size or raw.info.get('dev_head_t') is None:
            raise ValueError('Maxwell filtering requires MEG sensors and a device-to-head transform')
        raw=mne.preprocessing.maxwell_filter(raw,origin=(0.,0.,.04),verbose='ERROR')
    if op in {'epoch','autoreject','connectivity','time-frequency'}:
        if mne.pick_types(raw.info,eeg=True).size: raw.set_eeg_reference('average',verbose='ERROR')
        epochs=epoch(raw,args)
        if not len(epochs): raise ValueError('No valid epochs')
        if op=='autoreject':
            from autoreject import AutoReject
            if not epochs.get_montage(): epochs.set_montage('standard_1020',on_missing='raise')
            if len(epochs)<4: raise ValueError('AutoReject requires at least four epochs')
            epochs=AutoReject(n_interpolate=[1,2],cv=min(5,len(epochs)),n_jobs=1,random_state=args.seed,verbose=False).fit_transform(epochs)
        if op in {'epoch','autoreject'}: epochs.save(out/'processed-epo.fif',overwrite=True,verbose='ERROR')
        if op=='connectivity': connectivity(epochs,args,out)
        if op=='time-frequency':
            freqs=np.linspace(args.freq_min,args.freq_max,12)
            power,itc=epochs.compute_tfr(method='morlet',freqs=freqs,n_cycles=freqs/2,return_itc=True,average=True,verbose='ERROR')
            np.savez_compressed(out/'time_frequency.npz',power=power.data,itc=itc.data,frequencies=freqs,times=power.times)
    if op=='source':
        if not args.forward or not args.noise_cov: raise ValueError('Source reconstruction requires forward solution and noise covariance')
        if mne.pick_types(raw.info,eeg=True).size: raw.set_eeg_reference('average',projection=True,verbose='ERROR')
        forward=mne.read_forward_solution(args.forward,verbose='ERROR'); cov=mne.read_cov(args.noise_cov,verbose='ERROR')
        inverse=mne.minimum_norm.make_inverse_operator(raw.info,forward,cov,loose='auto',verbose='ERROR')
        source=mne.minimum_norm.apply_inverse_raw(raw,inverse,lambda2=1/9,method='dSPM',verbose='ERROR')
        arrays={'data':source.data,'times':source.times}
        arrays.update({'vertices_'+str(i):v for i,v in enumerate(source.vertices)})
        np.savez_compressed(out/'source.npz',**arrays)
    if op in {'inspect','bad-channels','filter','ica','maxwell'}:
        raw.save(out/'processed-raw.fif',overwrite=True,verbose='ERROR')
    summary['output_sfreq']=raw.info['sfreq']
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))


if __name__=='__main__': main()
