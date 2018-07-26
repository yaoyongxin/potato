import numpy as np
import scipy

import pyscf
import pyscf.cc
from pyscf.cc.eom_rccsd import amplitudes_to_vector_ip, amplitudes_to_vector_ea


###################
# EA Greens       #
###################


def greens_b_amplitudes_ea_rhf(t1, t2, p):
    nocc, nvir = t1.shape
    ds_type = t1.dtype
    if p < nocc:
        return -t1[p, :], -t2[p, :, :, :]
    else:
        vector1 = np.zeros((nvir,), dtype=ds_type)
        vector2 = np.zeros((nocc, nvir, nvir), dtype=ds_type)
        vector1[p - nocc] = 1.0
        return vector1, vector2


def greens_b_vector_ea_rhf(cc, p):
    return amplitudes_to_vector_ea(*greens_b_amplitudes_ea_rhf(cc.t1, cc.t2, p))


def greens_e_amplitudes_ea_rhf(t1, t2, l1, l2, p):
    nocc, nvir = t1.shape
    ds_type = t1.dtype
    if p < nocc:
        return l1[p, :], 2 * l2[p, :, :, :] - l2[:, p, :, :]
    else:
        vector1 = np.zeros((nvir,), dtype=ds_type)
        vector2 = np.zeros((nocc, nvir, nvir), dtype=ds_type)
        vector1[p - nocc] = -1.0
        vector1 += np.einsum('ia,i->a', l1, t1[:, p - nocc])
        vector1 += 2 * np.einsum('klca,klc->a', l2, t2[:, :, :, p - nocc])
        vector1 -= np.einsum('klca,lkc->a', l2, t2[:, :, :, p - nocc])

        vector2[:, p - nocc, :] += -2. * l1
        vector2[:, :, p - nocc] += l1
        vector2 += 2 * np.einsum('k,jkba->jab', t1[:, p - nocc], l2)
        vector2 -= np.einsum('k,jkab->jab', t1[:, p - nocc], l2)

        return vector1, vector2


def greens_e_vector_ea_rhf(cc, p):
    return amplitudes_to_vector_ea(*greens_e_amplitudes_ea_rhf(cc.t1, cc.t2, cc.l1, cc.l2, p))


###################
# IP Greens       #
###################


def greens_b_amplitudes_ip_rhf(t1, t2, p):
    nocc, nvir = t1.shape
    ds_type = t1.dtype
    if p < nocc:
        vector1 = np.zeros((nocc,), dtype=ds_type)
        vector2 = np.zeros((nocc, nocc, nvir), dtype=ds_type)
        vector1[p] = 1.0
        return vector1, vector2
    else:
        return t1[:, p - nocc], t2[:, :, :, p - nocc]


def greens_b_vector_ip_rhf(cc, p):
    return amplitudes_to_vector_ip(*greens_b_amplitudes_ip_rhf(cc.t1, cc.t2, p))


def greens_e_amplitudes_ip_rhf(t1, t2, l1, l2, p):
    nocc, nvir = t1.shape
    ds_type = t1.dtype
    if p < nocc:
        vector1 = np.zeros((nocc,), dtype=ds_type)
        vector2 = np.zeros((nocc, nocc, nvir), dtype=ds_type)
        vector1[p] = -1.0
        vector1 += np.einsum('ia,a->i', l1, t1[p, :])
        vector1 += 2 * np.einsum('ilcd,lcd->i', l2, t2[p, :, :, :])
        vector1 -= np.einsum('ilcd,ldc->i', l2, t2[p, :, :, :])

        vector2[p, :, :] += -2. * l1
        vector2[:, p, :] += l1
        vector2 += 2 * np.einsum('c,ijcb->ijb', t1[p, :], l2)
        vector2 -= np.einsum('c,jicb->ijb', t1[p, :], l2)

        return vector1, vector2
    else:
        return -l1[:, p - nocc], -2 * l2[:, :, p - nocc, :] + l2[:, :, :, p - nocc]


def greens_e_vector_ip_rhf(cc, p):
    return amplitudes_to_vector_ip(*greens_e_amplitudes_ip_rhf(cc.t1, cc.t2, cc.l1, cc.l2, p))


def greens_func_multiply(ham, vector, linear_part, **kwargs):
    return np.array(ham(vector, **kwargs) + linear_part * vector)


def initial_ip_guess(cc):
    nocc, nvir = cc.t1.shape
    vector1 = np.zeros((nocc,), dtype=complex)
    vector2 = np.zeros((nocc, nocc, nvir), dtype=complex)
    return amplitudes_to_vector_ip(vector1, vector2)


def initial_ea_guess(cc):
    nocc, nvir = cc.t1.shape
    vector1 = np.zeros((nvir,), dtype=complex)
    vector2 = np.zeros((nocc, nvir, nvir), dtype=complex)
    return amplitudes_to_vector_ea(vector1, vector2)


def ip_shape(cc):
    nocc, nvir = cc.t1.shape
    return nocc + nocc * nocc * nvir


def ea_shape(cc):
    nocc, nvir = cc.t1.shape
    return nvir + nocc * nvir * nvir


class greens_function:
    def __init__(self, verbose=0):
        self.verbose = verbose

    def td_ip_ao(self, cc, ps, times, mo_coeff, re_im="re", tol=1.e-5):
        """
        E0: total CC gs energy
        ti: initial time
        tf: final time
        times: list of times where GF is computed
        tol : rtol, atol for ODE integrator
                   
        mo_coeff : integrals are assumed in the MO basis
        they are supplied here so we can back transform
        to the AO basis

        re_im in {"re", "im"}

        Signs, etc. Defn. at https://edoc.ub.uni-muenchen.de/18937/1/Wolf_Fabian_A.pdf, pg. 141
        re_im = "re" corresponds to G^<(it) in Eq. A.3, with t = times
        re_im = "im" corresponds to G^<(\tau) in Eq. A.2, with \tau = times        
        """
        dtype = None
        tfac = None
        if re_im == "im":
            dtype = np.float64
            tfac = 1
        elif re_im == "re":
            dtype = np.complex128
            tfac = 1j
        else:
            raise RuntimeError

        # set initial bra/ket
        nmo = mo_coeff.shape[1]
        e_vector_mo = np.zeros([nmo, ip_shape(cc)], dtype=dtype)
        for i in range(nmo):
            e_vector_mo[i, :] = greens_e_vector_ip_rhf(cc, i)
        e_vector_ao = np.einsum("pi,ix->px", mo_coeff[ps, :], e_vector_mo)

        b_vector_mo = np.zeros([ip_shape(cc), nmo], dtype=dtype)
        for i in range(nmo):
            b_vector_mo[:, i] = greens_b_vector_ip_rhf(cc, i)
        b_vector_ao = np.einsum("xi,ip->xp", b_vector_mo, mo_coeff.T[:, ps])

        # initialize loop variables
        gf_ao = np.zeros((len(ps), len(ps), len(times)), dtype=dtype)
        eomip = pyscf.cc.eom_rccsd.EOMIP(cc)
        eomip_imds = eomip.make_imds()
        ti = times[0]
        tf = times[-1]

        def matr_multiply(t, vector):
            # note: t is a dummy time argument, H is time-independent
            return tfac * np.array(eomip.matvec(vector, imds=eomip_imds))

        for ip, p in enumerate(ps):
            solp = scipy.integrate.solve_ivp(matr_multiply, (ti, tf),
                                             b_vector_ao[:, p], t_eval=times,
                                             rtol=tol, atol=tol)

            for iq, q in enumerate(ps):
                gf_ao[iq, ip, :] = np.dot(e_vector_ao[iq], solp.y)

        return gf_ao

    def td_ea_ao(self, cc, ps, times, mo_coeff, re_im="re", tol=1.e-5):
        """
        See td_ip.
        
        Defn. at https://edoc.ub.uni-muenchen.de/18937/1/Wolf_Fabian_A.pdf, pg. 141
        corresponds to G^>(it) in Eq. A.3
        """
        dtype = None
        tfac = None
        if re_im == "im":
            dtype = np.float64
            tfac = -1
        elif re_im == "re":
            dtype = np.complex128
            tfac = -1j
        else:
            raise RuntimeError

        # set initial bra/ket
        nmo = mo_coeff.shape[1]
        e_vector_mo = np.zeros([nmo, ea_shape(cc)], dtype=dtype)
        for i in range(nmo):
            e_vector_mo[i, :] = greens_e_vector_ea_rhf(cc, i)
        e_vector_ao = np.einsum("pi,ix->px", mo_coeff[ps, :], e_vector_mo)

        b_vector_mo = np.zeros([ea_shape(cc), nmo], dtype=dtype)
        for i in range(nmo):
            b_vector_mo[:, i] = greens_b_vector_ea_rhf(cc, i)
        b_vector_ao = np.einsum("xi,ip->xp", b_vector_mo, mo_coeff.T[:, ps])

        # initialize loop variables
        gf_ao = np.zeros((len(ps), len(ps), len(times)), dtype=dtype)
        eomea = pyscf.cc.eom_rccsd.EOMEA(cc)
        eomea_imds = eomea.make_imds()
        ti = times[0]
        tf = times[-1]

        def matr_multiply(t, vector):
            # note: t is a dummy time argument, H is time-independent
            return tfac * np.array(eomea.matvec(vector, imds=eomea_imds))

        for ip, p in enumerate(ps):
            solp = scipy.integrate.solve_ivp(matr_multiply, (ti, tf),
                                             b_vector_ao[:, p], t_eval=times,
                                             rtol=tol, atol=tol)

            for iq, q in enumerate(ps):
                gf_ao[iq, ip, :] = np.dot(e_vector_ao[iq], solp.y)

        return gf_ao

    def td_ip(self, cc, ps, qs, times, re_im="re", tol=1.e-5):
        """
        E0: total CC gs energy
        ti: initial time
        tf: final time
        times: list of times where GF is computed
        tol : rtol, atol for ODE integrator
                   
        re_im in {"re", "im"}

        Signs, etc. Defn. at https://edoc.ub.uni-muenchen.de/18937/1/Wolf_Fabian_A.pdf, pg. 141
        re_im = "re" corresponds to G^<(it) in Eq. A.3, with t = times
        re_im = "im" corresponds to G^<(\tau) in Eq. A.2, with \tau = times        
        """
        dtype = None
        tfac = None
        if re_im == "im":
            dtype = np.float64
            tfac = 1
        elif re_im == "re":
            dtype = np.complex128
            tfac = 1j
        else:
            raise RuntimeError

        eomip = pyscf.cc.eom_rccsd.EOMIP(cc)
        eomip_imds = eomip.make_imds()
        ti = times[0]
        tf = times[-1]

        e_vector = list()
        for q in qs:
            e_vector.append(greens_e_vector_ip_rhf(cc, q))

        gfvals = np.zeros((len(ps), len(qs), len(times)), dtype=dtype)

        for ip, p in enumerate(ps):
            b_vector = np.array(greens_b_vector_ip_rhf(cc, p), dtype=dtype)

            def matr_multiply(t, vector, args=None):
                # note: t is a dummy time argument, H is time-independent
                res = tfac * np.array(eomip.matvec(vector, imds=eomip_imds))
                return res

            solp = scipy.integrate.solve_ivp(matr_multiply, (ti, tf),
                                             b_vector, t_eval=times, rtol=tol, atol=tol)

            for iq, q in enumerate(qs):
                gfvals[iq, ip, :] = np.dot(e_vector[iq], solp.y)

        return gfvals

    def td_ea(self, cc, ps, qs, times, re_im="re", tol=1.e-5):
        """
        See td_ip.

        Defn. at https://edoc.ub.uni-muenchen.de/18937/1/Wolf_Fabian_A.pdf, pg. 141
        corresponds to G^>(it) in Eq. A.3
        """
        dtype = None
        tfac = None
        if re_im == "im":
            dtype = np.float64
            tfac = -1
        elif re_im == "re":
            dtype = np.complex128
            tfac = -1j
        else:
            raise RuntimeError

        ti = times[0]
        tf = times[-1]
        eomea = pyscf.cc.eom_rccsd.EOMEA(cc)
        eomea_imds = eomea.make_imds()

        e_vector = list()
        for p in ps:
            e_vector.append(np.array(greens_e_vector_ea_rhf(cc, p), dtype=dtype))
        gfvals = np.zeros((len(ps), len(qs), len(times)), dtype=complex)

        for iq, q in enumerate(qs):
            b_vector = np.array(greens_b_vector_ea_rhf(cc, q), dtype=dtype)

            def matr_multiply(t, vector, args=None):
                # t is a dummy time argument
                res = tfac * np.array(eomea.matvec(vector, imds=eomea_imds))
                return res

            solq = scipy.integrate.solve_ivp(matr_multiply, (ti, tf),
                                             b_vector, t_eval=times, rtol=tol, atol=tol)

            for ip, p in enumerate(ps):
                gfvals[ip, iq, :] = np.dot(e_vector[ip], solq.y)
        return gfvals

    def solve_ip_ao(self, cc, ps, omega_list, mo_coeff, broadening):
        eomip = pyscf.cc.eom_rccsd.EOMIP(cc)
        eomip_imds = eomip.make_imds()
        # GKC: Why is this is the initial guess?
        x0 = initial_ip_guess(cc)
        p0 = 0.0 * x0 + 1.0

        # set initial bra/ket
        nmo = mo_coeff.shape[1]
        e_vector_mo = np.zeros([nmo, ip_shape(cc)], dtype=np.complex128)
        for i in range(nmo):
            e_vector_mo[i, :] = greens_e_vector_ip_rhf(cc, i)
        e_vector_ao = np.einsum("pi,ix->px", mo_coeff[ps, :], e_vector_mo)
        b_vector_mo = np.zeros([ip_shape(cc), nmo], dtype=np.complex128)
        for i in range(nmo):
            b_vector_mo[:, i] = greens_b_vector_ip_rhf(cc, i)
        b_vector_ao = np.einsum("xi,ip->xp", b_vector_mo, mo_coeff.T[:, ps])
        # initialize loop variables
        gf_ao = np.zeros((len(ps), len(ps), len(omega_list)), dtype=np.complex128)

        for ip, p in enumerate(ps):
            for iomega in range(len(omega_list)):
                curr_omega = omega_list[iomega]

                def matr_multiply(vector, args=None):
                    return greens_func_multiply(eomip.matvec, vector, curr_omega - 1j * broadening, imds=eomip_imds)

                sol = self.__solve_linear_problem__(matr_multiply, b_vector_ao[:, p], x0, p0)
                x0 = sol
                for iq, q in enumerate(ps):
                    gf_ao[ip, iq, iomega] = -np.dot(e_vector_ao[iq, :], sol)
        return gf_ao

    def solve_ea_ao(self, cc, ps, omega_list, mo_coeff, broadening):
        eomea = pyscf.cc.eom_rccsd.EOMEA(cc)
        eomea_imds = eomea.make_imds()
        # GKC: Why is this is the initial guess?
        x0 = initial_ea_guess(cc)
        p0 = 0.0 * x0 + 1.0

        # set initial bra/ket
        nmo = mo_coeff.shape[1]
        e_vector_mo = np.zeros([nmo, ea_shape(cc)], dtype=np.complex128)
        for i in range(nmo):
            e_vector_mo[i, :] = greens_e_vector_ea_rhf(cc, i)
        e_vector_ao = np.einsum("pi,ix->px", mo_coeff[ps, :], e_vector_mo)
        b_vector_mo = np.zeros([ea_shape(cc), nmo], dtype=np.complex128)
        for i in range(nmo):
            b_vector_mo[:, i] = greens_b_vector_ea_rhf(cc, i)
        b_vector_ao = np.einsum("xi,ip->xp", b_vector_mo, mo_coeff.T[:, ps])
        # initialize loop variables
        gf_ao = np.zeros((len(ps), len(ps), len(omega_list)), dtype=np.complex128)

        for iq, q in enumerate(ps):
            for iomega in range(len(omega_list)):
                curr_omega = omega_list[iomega]

                def matr_multiply(vector, args=None):
                    return greens_func_multiply(eomea.matvec, vector, -curr_omega - 1j * broadening, imds=eomea_imds)

                sol = self.__solve_linear_problem__(matr_multiply, b_vector_ao[:, q], x0, p0)
                x0 = sol
                for ip, p in enumerate(ps):
                    gf_ao[ip, iq, iomega] = np.dot(e_vector_ao[ip], sol)

        return gf_ao

    def solve_ip(self, cc, ps, qs, omega_list, broadening):
        eomip = pyscf.cc.eom_rccsd.EOMIP(cc)
        eomip_imds = eomip.make_imds()
        x0 = initial_ip_guess(cc)
        p0 = 0.0 * x0 + 1.0
        e_vector = list()
        for q in qs:
            e_vector.append(greens_e_vector_ip_rhf(cc, q))
        gfvals = np.zeros((len(ps), len(qs), len(omega_list)), dtype=complex)
        for ip, p in enumerate(ps):
            b_vector = greens_b_vector_ip_rhf(cc, p)
            for iomega in range(len(omega_list)):
                curr_omega = omega_list[iomega]

                def matr_multiply(vector, args=None):
                    return greens_func_multiply(eomip.matvec, vector, curr_omega - 1j * broadening, imds=eomip_imds)

                sol = self.__solve_linear_problem__(matr_multiply, b_vector, x0, p0)
                x0 = sol
                for iq, q in enumerate(qs):
                    gfvals[ip, iq, iomega] = -np.dot(e_vector[iq], sol)
        if len(ps) == 1 and len(qs) == 1:
            return gfvals[0, 0, :]
        else:
            return gfvals

    def solve_ea(self, cc, ps, qs, omega_list, broadening):
        eomea = pyscf.cc.eom_rccsd.EOMEA(cc)
        eomea_imds = eomea.make_imds()
        x0 = initial_ea_guess(cc)
        p0 = 0.0 * x0 + 1.0
        e_vector = list()
        for p in ps:
            e_vector.append(greens_e_vector_ea_rhf(cc, p))
        gfvals = np.zeros((len(ps), len(qs), len(omega_list)), dtype=complex)
        for iq, q in enumerate(qs):
            b_vector = greens_b_vector_ea_rhf(cc, q)
            for iomega in range(len(omega_list)):
                curr_omega = omega_list[iomega]

                def matr_multiply(vector, args=None):
                    return greens_func_multiply(eomea.matvec, vector, -curr_omega - 1j * broadening, imds=eomea_imds)

                sol = self.__solve_linear_problem__(matr_multiply, b_vector, x0, p0)
                x0 = sol
                for ip, p in enumerate(ps):
                    gfvals[ip, iq, iomega] = np.dot(e_vector[ip], sol)
        if len(ps) == 1 and len(qs) == 1:
            return gfvals[0, 0, :]
        else:
            return gfvals

    def solve_gf(self, cc, p, q, omega_list, broadening):
        return self.solve_ip(cc, p, q, omega_list, broadening), self.solve_ea(cc, p, q, omega_list, broadening)

    def __solve_linear_problem__(self, matr_multiply, b_vector, x0, p0):
        self.l("  Solving linear problem ...")
        try:
            import gminres
            driver = gminres.gMinRes(matr_multiply, b_vector, x0, p0)
            return driver.get_solution().reshape(-1)

        except ImportError:
            self.l("  gminres import failed, trying scipy ...")
            # TODO: p0 is not used here, whatever it is
            import scipy.sparse.linalg as spla
            size = len(b_vector)
            Ax = spla.LinearOperator((size, size), matr_multiply)
            result, info = spla.gmres(Ax, b_vector, x0=x0, callback=self.__spla_callback__)
            if info != 0:
                raise RuntimeError("Error solving linear problem: info={:d}".format(info))
            return result

    def l(self, x):
        if self.verbose > 0:
            print(x)

    def __spla_callback__(self, r):
        self.l("    res = {:.3e}".format(r))