import math
import numpy as np
import matlab.engine
from pyomo.environ import *
from pyomo.dae import *
from pyomo.gdp import *
from pyomo.gdp.plugins.chull import ConvexHull_Transformation
from pyomo.gdp.plugins.bigm import BigM_Transformation
from pyomo.core import Var
from pyomo.dae.plugins.finitedifference import Finite_Difference_Transformation
import hopperUtil

class Hopper:
    def __init__(self, N, eng, matlabHopper, name=''):
        self.model_disc = []
        self.positionMax = 10
        self.rotationMax = 2*np.pi
        self.velocityMax = 10
        self.angularVelocityMax = 10
        self.forceMax = 10
        self.N = N
        self.r = []
        self.v = []
        self.F = []
        self.th = []
        self.w = []
        self.T = []
        self.p = []
        self.pd = []
        self.R = []
        self.dtBounds = (0.05, 0.2)
        self.c = []
        self.p_MDT = -2
        self.P_MDT = -1
        self.regions = []
        self.base = 10
        self.tf = 1
        self.nOrientationSectors = 1
        self.bodyRadius = 0.5
        self.mdt_precision = 1
        self.eng = eng
        self.matlabHopper = matlabHopper
        self.momentOfInertia = self.eng.getDimensionlessMomentOfInertia(self.matlabHopper)
        self.hipOffset = self.eng.getHipInBody(self.matlabHopper)
        self.footnames = self.hipOffset.keys()

    def addPlatform(self, platform_start, platform_end, platform_height, mu):
        self.addRegion(A=np.matrix('-1., 0.,; 1., 0.'),
                       b=np.matrix('%f; %f' % (-platform_start, platform_end)),
                       Aeq=np.array([0., 1.]), beq=platform_height, normal=np.matrix('0.; 1.'),
                       mu=mu)
        self.eng.addPlatform(self.matlabHopper, platform_start, platform_end, platform_height, nargout=0)

    def addFreeBlock(self, left=None, right=None, top=None, bottom=None):
        Arows = []
        brows = []
        if left is not None:
            Arows.append(np.matrix('-1., 0.'))
            brows.append(np.matrix(-left))
        if right is not None:
            Arows.append(np.matrix('1., 0.'))
            brows.append(np.matrix(right))
        if top is not None:
            Arows.append(np.matrix('0., 1.'))
            brows.append(np.matrix(top))
        if bottom is not None:
            Arows.append(np.matrix('0., -1.'))
            brows.append(np.matrix(-bottom))
        self.addRegion(A=np.vstack(Arows), b=np.vstack(brows))


    def addRegion(self, **kwargs):
        self.regions.append(dict.fromkeys(['A', 'b', 'Aeq', 'beq', 'normal', 'mu']))
        self.regions[-1]['normal'] = np.matrix('0.; 0.')
        self.regions[-1]['mu'] = 0.
        for key, value in kwargs.iteritems():
            for key2 in self.regions[-1].keys():
                if key == key2:
                    self.regions[-1][key] = value

    def constructVisualizer(self):
        self.eng.constructVisualizer(self.matlabHopper, nargout=0)

    def playback(self, speed=1.):
        self.eng.playback(self.matlabHopper, speed, nargout=0)

    def loadResults(self, m):
        data = dict()
        data['t'] =     matlab.double(hopperUtil.extractTime(m).tolist())
        data['r'] =     matlab.double(hopperUtil.extractPostition(m).tolist())
        data['th'] =    matlab.double(hopperUtil.extractOrientation(m).tolist())
        data['r_hip'] = matlab.double(hopperUtil.extractHipPosition(m).tolist())
        data['p'] =     matlab.double(hopperUtil.extractRelativeFootPosition(m).tolist())
        data['f'] =     matlab.double(hopperUtil.extractFootForce(m).tolist())
        data['T'] =     matlab.double(hopperUtil.extractTotalTorque(m).tolist())
        self.eng.loadResults(self.matlabHopper, data, nargout=0)

    def constructPyomoModel(self):
        model = ConcreteModel()
        model.R2_INDEX = Set(initialize=['x', 'z'])
        model.feet = Set(initialize=self.footnames)
        model.REGION_INDEX = RangeSet(0, len(self.regions)-1)
        model.t = RangeSet(1, self.N)
        model.BV_INDEX = RangeSet(0, 1)

        def _bvRule(m, region, bv, xz):
            # v = rot(+-atan(mu))*normal
            mu = self.regions[region]['mu']
            if bv == m.BV_INDEX[1]:
                theta = np.arctan(mu)
            else:
                theta = -np.arctan(mu)
            R = np.matrix([[cos(theta), -sin(theta)],
                          [sin(theta),  cos(theta)]])
            vec = R*(self.regions[region]['normal'])
            if xz == 'x':
                return float(vec[0])
            else:
                return float(vec[1])
        model.basisVectors = Param(model.REGION_INDEX, model.BV_INDEX, model.R2_INDEX, initialize=_bvRule)

        def _hipOffsetRule(m, foot, xz):
            return self.hipOffset[foot][xz]
        model.hipOffset = Param(model.feet, model.R2_INDEX, initialize=_hipOffsetRule)

        model.dt = Var(model.t, bounds=self.dtBounds, initialize=float(np.mean(self.dtBounds)))
        model.r = Var(model.R2_INDEX, model.t, bounds=(-self.positionMax, self.positionMax))
        model.v = Var(model.R2_INDEX, model.t, bounds=(-self.velocityMax, self.velocityMax))
        model.th = Var(model.t, bounds=(-self.rotationMax, self.rotationMax))
        model.w = Var(model.t, bounds=(-self.angularVelocityMax, self.angularVelocityMax))
        model.F = Var(model.R2_INDEX, model.t, bounds=(-self.forceMax, self.forceMax))
        model.f = Var(model.feet, model.R2_INDEX, model.t, bounds=(-self.forceMax, self.forceMax))
        model.hipTorque = Var(model.feet, model.t, bounds=(-self.forceMax, self.forceMax))
        #model.beta = Var(model.feet, model.BV_INDEX, model.t, within=NonNegativeReals, bounds=(0, self.forceMax))
        model.T = Var(model.t, bounds=(-self.forceMax, self.forceMax))
        lb = {'x': -1, 'z': -1}
        ub = {'x':  1, 'z': -0.5}
        def _pBounds(m, foot, i, t):
            return (math.sqrt(2)/2*lb[i], math.sqrt(2)/2*ub[i])
        model.p = Var(model.feet, model.R2_INDEX, model.t, bounds=_pBounds)
        model.pd = Var(model.feet, model.R2_INDEX, model.t, bounds=(-self.velocityMax, self.velocityMax))
        model.pdd = Var(model.feet, model.R2_INDEX, model.t, bounds=(-self.velocityMax, self.velocityMax))
        model.hip = Var(model.feet, model.R2_INDEX, model.t, bounds=(-1, 1))
        model.footRelativeToCOM = Var(model.feet, model.R2_INDEX, model.t, bounds=(-1, 1))
        model.foot = Var(model.feet, model.R2_INDEX, model.t, bounds=(-self.positionMax, self.positionMax))
        model.cth = Var(model.t, bounds=(-1,1))
        model.sth = Var(model.t, bounds=(-1,1))

        # Fix final dt to zero
        model.dt[model.t[-1]].value = 0.0
        model.dt[model.t[-1]].fixed = True


        # Constraints for BRF vectors
        # to avoid warnings, we set breakpoints at or beyond the bounds
        numPieces = self.nOrientationSectors
        bpts = []
        for i in range(numPieces+2):
            bpts.append(float(-self.rotationMax + (i*2*self.rotationMax)/numPieces))

        def _cos(model, t, th):
            return cos(th)

        def _sin(model, t, th):
            return sin(th)

        model.pwCos = Piecewise(model.t, model.cth, model.th, pw_pts=bpts, pw_constr_type='EQ', pw_repn='CC', f_rule=_cos)
        model.pwSin = Piecewise(model.t, model.sth, model.th, pw_pts=bpts, pw_constr_type='EQ', pw_repn='CC', f_rule=_sin)

        def _momentRule(m, t):
            return m.T[t] == sum(m.footRelativeToCOM[foot,'x',t]*m.f[foot,'z',t] - m.footRelativeToCOM[foot,'z',t]*m.f[foot, 'x',t] for foot in m.feet)

        model.momentAbountCOM = Constraint(model.t, rule=_momentRule)

        #def _hipTorqueRule(m, foot, t):
            #return m.hipTorque[foot, t] == m.p[foot,'x',t]*m.f[foot,'z',t] - m.p[foot,'z',t]*m.f[foot, 'x',t]

        #model.hipTorqueConstraint = Constraint(model.feet, model.t, rule=_hipTorqueRule)

        def _forceRule(m, i, t):
            g = -1 if i == 'z' else 0
            return m.F[i,t] == sum(m.f[foot, i, t] for foot in m.feet) + g
        model.totalForce = Constraint(model.R2_INDEX, model.t, rule=_forceRule)

#         def _legLengthRule(m, foot, t):
#             return m.p[foot, 'x', t]**2 + m.p[foot, 'z', t]**2 <= 1
        #model.legLengthConstraint = Constraint(model.feet, model.t, rule=_legLengthRule)

        # Translational dynamics
        def _positionRule(m, i, t):
            if t == self.N:
                return Constraint.Skip
            else:
                v_mid = m.v[i,t] + m.dt[t]/2*m.F[i,t]
                return m.r[i,t+1] == m.r[i, t] + m.dt[t]*v_mid
        model.positionConstraint = Constraint(model.R2_INDEX, model.t, rule=_positionRule)

        def _footPositionDefinition(m, foot, i, t):
            return m.foot[foot, i, t] == m.p[foot, i, t] + m.hip[foot, i, t] + m.r[i, t]
        model.footPositionDefinition = Constraint(model.feet, model.R2_INDEX, model.t, rule=_footPositionDefinition)

        def _footPositionRule(m, foot, i, t):
            if t == self.N:
                return Constraint.Skip
            else:
                return m.foot[foot, i, t+1] == m.foot[foot, i, t] + m.dt[t]*m.pd[foot, i, t]
        model.footPositionConstraint = Constraint(model.feet, model.R2_INDEX, model.t, rule=_footPositionRule)

        def _footRelativeToCOMDefinition(m, foot, xz, t):
            return m.footRelativeToCOM[foot, xz, t] == m.p[foot, xz, t] + m.hip[foot, xz, t]
        model.footRelativeToCOMDefinition = Constraint(model.feet, model.R2_INDEX, model.t, rule=_footRelativeToCOMDefinition)

        # Hip position
        #  r_hip == [cth, sth; -sth, cth]*hip
        #  r_hip == [cth*hip(1) + sth*hip(2); -sth*hip(1) + cth*hip(2)]
        #  r_hip == [hip(1), hip(2); hip(2), -hip(1)]*[cth; sth]
        def _hipPositionRule(m, foot, xz, t):
            if xz == 'x':
                return m.hip[foot, xz, t] == m.hipOffset[foot, 'x']*m.cth[t] + m.hipOffset[foot, 'z']*m.sth[t]
            else:
                return m.hip[foot, xz, t] == m.hipOffset[foot, 'z']*m.cth[t] - m.hipOffset[foot, 'x']*m.sth[t]
        model.hipPositionConstraint = Constraint(model.feet, model.R2_INDEX, model.t, rule=_hipPositionRule)

        def _footVelocityRule(m, foot, xz, t):
            if t == self.N:
                return Constraint.Skip
            else:
                return m.pd[foot, xz, t + 1] == m.pd[foot, xz, t] + 0.5*m.dt[t]*(m.pdd[foot, xz, t] + m.pdd[foot, xz, t + 1])
        model.footVelocityConstraint = Constraint(model.feet, model.R2_INDEX, model.t, rule=_footVelocityRule)

        def _velocityRule(m, i, t):
            if t == self.N:
                return Constraint.Skip
            else:
                v_mid = m.v[i,t] + m.dt[t]/2*m.F[i,t]
                return m.v[i,t+1] == v_mid + m.dt[t]/2*m.F[i,t+1]

        model.velocityConstraint = Constraint(model.R2_INDEX, model.t, rule=_velocityRule)

        def _angularVelocityRule(m, t):
            if t == self.N:
                return Constraint.Skip
            else:
                w_mid = m.w[t] + m.dt[t]/(2*self.momentOfInertia)*m.T[t]
                return m.w[t+1] == w_mid + m.dt[t]/(2*self.momentOfInertia)*m.T[t+1]

        model.angularVelocityConstraint = Constraint(model.t, rule=_angularVelocityRule)

        def _orientationRule(m, t):
            if t == self.N:
                return Constraint.Skip
            else:
                w_mid = m.w[t] + m.dt[t]/(2*self.momentOfInertia)*m.T[t]
                return m.th[t+1] == m.th[t] + m.dt[t]*w_mid

        model.orientationConstraint = Constraint(model.t, rule=_orientationRule)

        def _footRegionConstraints(disjunct, region, foot, t):
            m = disjunct.model()
            A = None
            if self.regions[region]['A'] is not None:
                A = self.regions[region]['A']
                b = self.regions[region]['b']
            if self.regions[region]['Aeq'] is not None:
                Aeq = self.regions[region]['Aeq']
                beq = self.regions[region]['beq']
                if A is not None:
                    A = np.vstack((A, Aeq, -Aeq))
                    b = np.vstack((b, beq, -beq))
                else:
                    A = np.vstack((Aeq, -Aeq))
                    b = np.vstack((beq, -beq))
            A = np.atleast_2d(A)
            b = np.atleast_1d(b)
            def _contactPositionConstraint(disjunctData, i):
                m = disjunctData.model()
                return A[i,0]*m.foot[foot, 'x', t] + A[i,1]*m.foot[foot, 'z', t] <= float(b[i])
            disjunct.contactPositionConstraint = Constraint(range(A.shape[0]), rule=_contactPositionConstraint)

            def _hipPositionConstraint(disjunctData, i):
                if self.regions[region]['mu'] == 0.:
                    m = disjunctData.model()
                    return A[i,0]*(m.r['x', t] + m.hip[foot, 'x', t]) + A[i,1]*(m.r['z', t] + m.hip[foot, 'z', t]) <= float(b[i])
                else:
                    return Constraint.Skip
            disjunct.hipPositionConstraint = Constraint(range(A.shape[0]), rule=_hipPositionConstraint)

            def _contactForceConstraint(disjunctData, xz):
                m = disjunctData.model()
                return m.f[foot, xz, t] == sum(m.beta[foot, bv, t]*m.basisVectors[region, bv, xz] for bv in m.BV_INDEX)
            #disjunct.contactForceConstraint = Constraint(m.R2_INDEX, rule=_contactForceConstraint)

            disjunct.contactForceConstraint1 = Constraint(expr=m.f[foot, 'x', t] <= self.regions[region]['mu']*m.f[foot, 'z', t])
            disjunct.contactForceConstraint2 = Constraint(expr=m.f[foot, 'x', t] >= -self.regions[region]['mu']*m.f[foot, 'z', t])
            def _contactForceConstraint3(disjunctData, xz):
                m = disjunctData.model()
                if self.regions[region]['mu'] == 0.:
                    return m.f[foot, xz, t] == 0
                else:
                    return Constraint.Skip
            disjunct.contactForceConstraint3 = Constraint(m.R2_INDEX, rule=_contactForceConstraint3)


            def _stationaryFootConstraint(disjunctData, xz):
                m = disjunctData.model()
                if self.regions[region]['mu'] != 0.:
                    if xz == 'x':
                        return m.pd[foot, xz, t] == 0
                    else:
                        return Constraint.Skip
                else:
                    return Constraint.Skip
            disjunct.stationaryFootConstraint = Constraint(m.R2_INDEX, rule=_stationaryFootConstraint)


        model.footRegionConstraints = Disjunct(model.REGION_INDEX, model.feet, model.t, rule=_footRegionConstraints)

        # Define the disjunction
        def _footRegionDisjunction(m, foot, t):
            disjunctList = []
            for region in m.REGION_INDEX:
                disjunctList.append(m.footRegionConstraints[region, foot, t])
            return disjunctList
        model.footRegionDisjunction = Disjunction(model.feet, model.t, rule=_footRegionDisjunction)

        def _bodyRegionConstraints(disjunct, region, t):
            if self.regions[region]['mu'] != 0.:
                return Constraint.Skip
            else:
                m = disjunct.model()
                A = None
                if self.regions[region]['A'] is not None:
                    A = self.regions[region]['A']
                    b = self.regions[region]['b'] - self.bodyRadius
                A = np.atleast_2d(A)
                b = np.atleast_1d(b)
                def _bodyPositionConstraint(disjunctData, i):
                    m = disjunctData.model()
                    return A[i,0]*m.r['x', t] + A[i,1]*m.r['z', t] <= float(b[i])
                disjunct.bodyPositionConstraint = Constraint(range(A.shape[0]), rule=_bodyPositionConstraint)


        model.bodyRegionConstraints = Disjunct(model.REGION_INDEX, model.t, rule=_bodyRegionConstraints)

        # Define the disjunction
        def _bodyRegionDisjunction(m, t):
            disjunctList = []
            for region in m.REGION_INDEX:
                if self.regions[region]['mu'] == 0.:
                    disjunctList.append(m.bodyRegionConstraints[region, t])
            return disjunctList
        #model.bodyRegionDisjunction = Disjunction(model.t, rule=_bodyRegionDisjunction)

        disjunctionTransform = ConvexHull_Transformation()
#         disjunctionTransform = BigM_Transformation()
        disjunctionTransform.apply_to(model)

        return model

def testHopper(hopper, r0, rf, legLength):
    hopper.constructPyomoModel()
    m_nlp = hopper.model

    def objRule(m):
        #     return sum(m.beta[foot, bv, ti]**2 for foot in m.feet for bv in m.BV_INDEX for ti in m.t)
        #     + sum(m.pdd[foot, i, j]**2 for foot in m.feet for i in m.R2_INDEX for j in m.t)
            return sum(m.f[foot, i, j]**2 + m.pdd[foot, i, j]**2 for foot in m.feet for i in m.R2_INDEX for j in m.t) + sum(m.T[ti]**2 for ti in m.t)

    m_nlp.Obj = Objective(rule=objRule, sense=minimize)

    m_nlp.rx0 = Constraint(expr=m_nlp.r['x',m_nlp.t[1]] == r0[0]/legLength)
    #m_nlp.rz0 = Constraint(expr=m_nlp.r['z',m_nlp.t[1]] <= 1)

    m_nlp.th0 = Constraint(expr=m_nlp.th[m_nlp.t[1]] == 0)

    m_nlp.vx0 = Constraint(expr=m_nlp.v['x',m_nlp.t[1]] == 0)
    m_nlp.vz0 = Constraint(expr=m_nlp.v['z',m_nlp.t[1]] == 0)

    m_nlp.w0 = Constraint(expr=m_nlp.w[m_nlp.t[1]] == 0)

    #m_nlp.Fx0 = Constraint(expr=m_nlp.F['x', m_nlp.t[1]] == 0)
    #m_nlp.Fz0 = Constraint(expr=m_nlp.F['z', m_nlp.t[1]] == 0)
    #m_nlp.T0 = Constraint(expr=m_nlp.T[m_nlp.t[1]] == 0)

    m_nlp.rxf = Constraint(expr=m_nlp.r['x',m_nlp.t[-1]] >= rf[0]/legLength)
    m_nlp.rzf = Constraint(expr=m_nlp.r['z',m_nlp.t[-1]] == m_nlp.r['z', m_nlp.t[1]])

    m_nlp.thf = Constraint(expr=m_nlp.th[m_nlp.t[-1]] == 0)

    m_nlp.vxf = Constraint(expr=m_nlp.v['x',m_nlp.t[-1]] == m_nlp.v['x',m_nlp.t[1]])
    m_nlp.vzf = Constraint(expr=m_nlp.v['z',m_nlp.t[-1]] == 0)

    m_nlp.wf = Constraint(expr=m_nlp.w[m_nlp.t[-1]] == 0)

    m_nlp.Fxf = Constraint(expr=m_nlp.F['x', m_nlp.t[-1]] == 0)
    m_nlp.Fzf = Constraint(expr=m_nlp.F['z', m_nlp.t[-1]] == 0)
    m_nlp.Tf = Constraint(expr=m_nlp.T[m_nlp.t[-1]] == 0)

    def _maxVerticalVelocityRule(m, t):
        return m.v['z', t] <= 0.5

    # m_nlp.maxVerticalVelocityConstraint = Constraint(m_nlp.t, rule=_maxVerticalVelocityRule)

    def _periodicFootPosition(m, foot, xz):
        return m.p[foot, xz, m.t[1]] == m.p[foot, xz, m.t[-1]]

    #m_nlp.periodicFootPosition = Constraint(m_nlp.feet, m_nlp.R2_INDEX, rule=_periodicFootPosition)

    return m_nlp

    #opt = SolverFactory('_gurobi_direct')
    # opt.set_options('mipgap=0.05')
    #if timeout > 0:
        #opt.set_options('TimeLimit=%f' % timeout)
    #opt.set_options('Threads=%f' % threads)
    # opt.set_options('Seed=0')
    #opt.set_options('Presolve=2')

    #return m, hopper, opt