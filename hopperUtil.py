from __future__ import division
import numpy as np
from pyomo.opt import SolverFactory
from pyomo.core.plugins.transform.radix_linearization import *
from mccormick_envelope import *
from pyomo.core.base.component import register_component, Component, ComponentUID


def constructRelaxedModel(m_nlp, dt=None):
    m = m_nlp.clone()
    if dt is not None:
        m.dt.fix(dt)
    else:
        m.dt.fix()
    mccormick = McCormickEnvelope()
    m = mccormick.create_using(m, verbose=True)
    return m

def constructMDTModel(m_nlp, desiredPrecision, dt=None):
    m = m_nlp.clone()
    if dt is not None:
        m.dt.fix(dt)
    else:
        m.dt.fix()
    RadixLinearization.radix = 2
    mdt = RadixLinearization()
    #discretizationVar = m.footRelativeToCOM
    discretizationVar = m.f
    fBounds = m.f.values()[0].bounds
    print fBounds
    footBounds = m.footRelativeToCOM.values()[0].bounds
    print footBounds
    maxVal = (fBounds[1] - fBounds[0])*(footBounds[1] - footBounds[0])
    precision = 1+0*int(np.ceil(-np.log2(desiredPrecision/maxVal)))
    print 'MDT precision: %d' % precision
    m = mdt.create_using(m, verbose=True, precision=precision, discretize=[discretizationVar])

    for z_data in m.z.values():
        z_data._component().branchPriority = 1
    return m

def constructCouenneSolver(**kwargs):
    opt = SolverFactory('couenne')
    return opt

def constructMinotaurSolver():
    opt = SolverFactory('qpd')
    opt.set_options('--ampl=1')
    opt.set_options('--nlp_engine=IPOPT')
    opt.set_options('--bnb_time_limit=3600.')
    # opt.set_options('--linfpump=1')
    return opt

def constructGurobiSolver(**kwargs):
    opt = SolverFactory('_gurobi_direct')
    for key, value in kwargs.iteritems():
        opt.set_options('%s=%f' % (key, value))
    return opt
    # opt.set_options('mipgap=0.05')
    #if timeout > 0:
        #opt.set_options('TimeLimit=%f' % timeout)
    #opt.set_options('Threads=%f' % threads)
    # opt.set_options('Seed=0')
    #opt.set_options('Presolve=2')

def extractTime(m):
    return np.cumsum([0.]+[m.dt[ti].value for ti in m.t][:-1])

def extractPostition(m):
    return np.vstack([np.array([m.r[xz, ti].value for ti in m.t]) for xz in m.R2_INDEX])

def extractOrientation(m):
    return np.atleast_2d(np.array([m.th[ti].value for ti in m.t]))

def extractHipPosition(m):
    return np.dstack([np.vstack([np.array([m.hip[foot, xz, ti].value for ti in m.t]) for xz in m.R2_INDEX]) for foot in m.feet])

def extractRelativeFootPosition(m):
    return np.dstack([np.vstack([np.array([m.p[foot, xz, ti].value for ti in m.t]) for xz in m.R2_INDEX]) for foot in m.feet])

def extractFootForce(m):
    return np.dstack([np.vstack([np.array([m.f[foot, xz, ti].value for ti in m.t]) for xz in m.R2_INDEX]) for foot in m.feet])

def extractTotalTorque(m):
    return np.atleast_2d(np.array([m.T[ti].value for ti in m.t]))

def extractRegionIndicators(m):
    return np.dstack([np.vstack([np.array([getattr(m, '%sindicator_var' % m.footRegionConstraints[region, foot, ti].cname()).value for ti in m.t]) for region in m.REGION_INDEX]) for foot in m.feet])

def extractBodyRegionIndicators(m, hop):
    def extractIndicatorForRegion(region):
        if hop.regions[region]['mu'] == 0.0:
            return np.array([getattr(m, '%sindicator_var' % m.bodyRegionConstraints[region, ti].cname()).value for ti in m.t])
        else:
            return np.zeros([1, len(m.t)])

    return np.vstack([extractIndicatorForRegion(region) for region in m.REGION_INDEX])

def fixIntegerVariables(m):
    for var in m.component_data_objects(Var):
        if not var.is_continuous():
            #print 'Fixing %s to %s' % (ComponentUID(var), var.value)
            var.fixed = True

def unfixIntegerVariables(m):
    for var in m.component_data_objects(Var):
        if not var.is_continuous():
            #print 'Fixing %s to %s' % (ComponentUID(var), var.value)
            var.fixed = False
