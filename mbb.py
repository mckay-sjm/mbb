### Class to implement a modified blackbody SED, with the capability to fit the SED with various options
# and plot a simple version of it, as well as save the results to a file. 
# Author: Stephen McKay, Spring 2023


# Imports

import matplotlib.pyplot as plt
import numpy as np
import re
from multiprocessing import Pool

import emcee
import corner

from astropy.table import Table, QTable
from astropy.io import fits
import astropy.units as u
import astropy.constants as con
from astropy.constants import c, k_B, h
from astropy.cosmology import FlatLambdaCDM
cosmo = FlatLambdaCDM(H0=70.0, Om0=0.30) 


Tcmb0 = 2.75

NWALKERS = 180
NITER = 2000
NBURN = 300
STEPSIZE = 1e-7

CURRENT_Z = 0

LLO = 8
LHI = 1000

#####################################################
# NEXT TODO: need to remove units from mbb_funcs and only assign or remove them outside
######################################################

from mbb_model.mbb_funcs import mbb_fun_ot, mbb_fun_go, mbb_fun_go_pl, mbb_fun_ot_pl, planckbb

class ModifiedBlackBody:

    def __init__(self, L, T, beta, z, opthin=True, pl=False):
        """Class to represent a modified blackbody (MBB) SED fit roughly following Casey et al. (2012),
         which can be plotted or fit to photometry."""
        self.L = L
        self.T = T 
        self.beta = beta 
        self.z = z
        self.pl = pl
        self.opthin=opthin
        self.model = self._select_model()

        self.N = 11
        Lcurr = np.log10(self.get_luminosity((8,1000)).value)
        while((Lcurr > (L+0.0001)) | (Lcurr < (L-0.0001))):
            self.N = self.N + 0.1*(L-Lcurr)
            Lcurr = np.log10(self.get_luminosity((8,1000)).value)
        self.L = np.round(Lcurr,2)

    def fit(self, phot, nwalkers=400, niter=2000, stepsize=1e-7):
        """
        Fit a modified blackbody to rest-frame photometry in Janskys, wavelengths in microns.
        Returns a mbb instance with the best-fit parameters of the fit.
        """
        fitwl = np.asarray(phot[0])
        fitflux = np.asarray(phot[1])
        fiterr = np.asarray(phot[2])
        self.phot = (fitwl,fitflux,fiterr)
        init = [self.N,self.T,self.beta]

        if len(phot) < 3:
            init = init[0:2]
        ndim=len(init)
        p0 = [np.array(init) + stepsize * np.random.randn(ndim) for i in range(nwalkers)]
        result = self._run_fit(p0=p0, nwalkers=nwalkers, niter=niter, lnprob=self._lnprob, 
            ndim=ndim, data = self.phot)
        self.result = result
        medtheta = self._get_med_theta(result)
        self.update(*medtheta[1])

    def update_L(self, L, T, beta):
        ''' update modified blackbody parameters (not redshift or model)'''
        self.T = T 
        self.beta = beta
        Lcurr = np.log10(self.get_luminosity((8,1000)).value)
        while((Lcurr > (L+0.001)) | (Lcurr < (L-0.001))):
            self.N = self.N * (L/Lcurr)
            Lcurr = np.log10(self.get_luminosity((8,1000)).value)
        self.L = np.round(Lcurr,2)

    def update(self, N, T, beta):
        ''' update modified blackbody parameters (not redshift or model)'''
        self.N = N
        self.T = T 
        self.beta = beta
        self.L = np.log10(self.get_luminosity((8,1000)).value)

    def save(self, filepath):
        '''write string version of fit to file that can be used to reinitialize'''
        raise NotImplementedError()

    def load_from_file(filepath):
        '''initialize fit from file'''
        raise NotImplementedError()

    def plot_sed(self, obs_frame=False):
        '''plot the rest-frame form of this mbb just for basic visualization. It is recommended 
        to use a separate, more detailed plotting function for figures.'''
        fig, ax = plt.subplots()
        x = np.logspace(1,3.5,200)
        y = 1000 * self.eval(x)
        if obs_frame == True:
            ax.set(xlabel = r'$\lambda$ observed-frame [$\mu$m]', ylabel = 'Flux [mJy]')
        else:
            x /= (1.+self.z)
            ax.set(xlabel = r'$\lambda$ rest-frame [$\mu$m]', ylabel = 'Flux [mJy]')
        ax.plot(x,y, ls='--',linewidth=1.0)
        
        if hasattr(self,'phot'):
            #initialize fitting arrays
            if obs_frame == True:
                fit_wl = self.phot[0]
            else:
                fit_wl = self.phot[0] / (1+self.z)
            fit_flux = 1000*self.phot[1] #mJy
            fit_err = 1000*self.phot[2]
            # check for nondetections and or incorrect input
            mask = (fit_wl < 0) | (fit_flux < 0) | (fit_err < 0)
            fit_wl = fit_wl[~mask]
            fit_flux = fit_flux[~mask]
            fit_err = fit_err[~mask]
            ax.errorbar(fit_wl, fit_flux, fit_err, 
                        c='r', ls='', marker = 'o', ms = 5,
                        elinewidth=0.5, capsize = 1.5, ecolor = 'k')
        ax.set(xscale='log', yscale='log')
        ax.set(xlim = (x.min(), x.max()*1.1), ylim=(1e-1,2e2))
        ax.annotate(f'z = {np.round(self.z,2)}', xy=(0.02, 0.90), xycoords = 'axes fraction')
        ax.annotate(f'beta = {np.round(self.beta,2)}', xy=(0.02, 0.85), xycoords = 'axes fraction')
        ax.annotate(f'T = {np.round(self.T,1)} K', xy=(0.02, 0.80), xycoords = 'axes fraction')
        return fig, ax
    
    def plot_corner(self):
        raise NotImplementedError()

    def eval(self, wl,z=0):
        """Return evaulation of this MBB's function if observed at the given wavelengths wl
        shifted to redshift z, in Jy. Leave z=0 to get rest-frame evaluation."""
        p = [self.N,self.T,self.beta]
        return self.model(p, wl/(1+z), z=z)*u.Jy

    def get_luminosity(self, wllimits, cosmo=FlatLambdaCDM(H0=70.0, Om0=0.30)):
        """get integrated LIR luminosity between wl limits in microns"""
        if len(wllimits) == 2 and wllimits[0] < wllimits[1]:
            nulow = (con.c/(wllimits[1]*u.um)).to(u.Hz)
            nuhigh = (con.c/(wllimits[0]*u.um)).to(u.Hz)
            nu = np.linspace(nulow, nuhigh, 20000)
            dnu = nu[1:] - nu[0:-1]
            DL = cosmo.luminosity_distance(self.z)
            lam = nu.to(u.um, equivalencies=u.spectral()).value  
            lum = np.sum(4*np.pi*DL**2 * self.eval(lam[:-1]) * dnu)/(1+self.z)
            return lum.to(u.Lsun)
    
    def _run_fit(self, p0,nwalkers,niter,ndim,lnprob,data):
        with Pool() as pool:
            sampler = emcee.EnsembleSampler(nwalkers, ndim, lnprob, args=data, pool=pool)
            print("Running burn-in...")
            p0, _, _ = sampler.run_mcmc(p0, NBURN,progress=True)
            sampler.reset()
            print("Running production...")
            pos, prob, state = sampler.run_mcmc(p0, niter,progress=True)
            print("Done\n")
            return {'sampler':sampler, 'pos':pos, 'prob':prob, 'state':state}  
    
    def _get_model_spread(self, lam, nsamples, flattened_chain, z):
        models = []
        draw = np.floor(np.random.uniform(0,len(flattened_chain),size=nsamples)).astype(int)
        thetas = flattened_chain[draw]
        for i in thetas:
            mod = self.model(i,lam,z=z)
            models.append(mod)
        spread = np.std(models, axis=0)
        lb,med_model,ub = np.percentile(models,[16,50,84],axis=0)
        return med_model, lb, ub

    def _get_med_theta(self, result):
        thetas = result['sampler'].flatchain
        theta_res = np.percentile(thetas,[16,50,84],axis=0)
        return theta_res

    def _select_model(self):
        if self.opthin:
            if self.pl: return mbb_fun_ot_pl
            else: return mbb_fun_ot
        else:
            if self.pl: return mbb_fun_go_pl
            else: return mbb_fun_go

    def _lnlike(self, theta, x,y,yerr):
        yerr[yerr==0]=-1
        ymodel = self.model(theta,x, z=self.z)
        wres = np.sum(((y-ymodel)/yerr)**2)
        lnlike = -0.5*wres
        if np.isnan(lnlike):
            return -np.inf
        return lnlike
        
    def _lnprior(self,theta):
        #assign variable parameter values
        Nbb = theta[0] # norm constant for greybody (gb)
        T = theta[1] # temperature of gb in K    
        if T > 10 and T < 100:
            if len(theta) > 2: 
                beta = theta[2] # emissivity index (set to p[2] if enough data points in FIR)
                if beta > 5.0 or beta < 0.1:
                    return -np.inf
            return 0.0
        else: return -np.inf 

    def _lnprob(self, theta, x,y,yerr):
        lp = self._lnprior(theta)
        if not np.isfinite(lp):
            return -np.inf
        return lp + self._lnlike(theta, x,y,yerr)





