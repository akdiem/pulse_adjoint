#!/usr/bin/env python
# c) 2001-2017 Simula Research Laboratory ALL RIGHTS RESERVED
# Authors: Henrik Finsberg
# END-USER LICENSE AGREEMENT
# PLEASE READ THIS DOCUMENT CAREFULLY. By installing or using this
# software you agree with the terms and conditions of this license
# agreement. If you do not accept the terms of this license agreement
# you may not install or use this software.

# Permission to use, copy, modify and distribute any part of this
# software for non-profit educational and research purposes, without
# fee, and without a written agreement is hereby granted, provided
# that the above copyright notice, and this license agreement in its
# entirety appear in all copies. Those desiring to use this software
# for commercial purposes should contact Simula Research Laboratory AS: post@simula.no
#
# IN NO EVENT SHALL SIMULA RESEARCH LABORATORY BE LIABLE TO ANY PARTY
# FOR DIRECT, INDIRECT, SPECIAL, INCIDENTAL, OR CONSEQUENTIAL DAMAGES,
# INCLUDING LOST PROFITS, ARISING OUT OF THE USE OF THIS SOFTWARE
# "PULSE-ADJOINT" EVEN IF SIMULA RESEARCH LABORATORY HAS BEEN ADVISED
# OF THE POSSIBILITY OF SUCH DAMAGE. THE SOFTWARE PROVIDED HEREIN IS
# ON AN "AS IS" BASIS, AND SIMULA RESEARCH LABORATORY HAS NO OBLIGATION
# TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS, OR MODIFICATIONS.
# SIMULA RESEARCH LABORATORY MAKES NO REPRESENTATIONS AND EXTENDS NO
# WARRANTIES OF ANY KIND, EITHER IMPLIED OR EXPRESSED, INCLUDING, BUT
# NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY OR FITNESS
import numpy as np
import pulse
from pulse.numpy_mpi import *


from .dolfinimport import *
from .utils import Object, Text, print_line, print_head
from .adjoint_contraction_args import *
from .setup_parameters import *


def merge_control(patient, control_str):

    sfun = MeshFunction("size_t", patient.mesh, patient.sfun.dim())
    sfun.set_values(patient.sfun.array())
    if control_str != "":
        for v in control_str.split(":"):
            vals = sorted(np.array(v.split(","), dtype=int))
            min_val = vals[0]
            for vi in vals[1:]:
                sfun.array()[sfun.array() == vi] = min_val

    return sfun


def get_material_model(material_model):

    assert material_model in pulse.material_model_names
    idx = pulse.material_model_names.index(material_model)
    return pulse.material_models[idx]


def update_unloaded_patient(params, patient):

    # Make sure to load the new referece geometry
    from mesh_generation import load_geometry_from_h5

    h5group = "/".join([_f for _f in [params["h5group"], "unloaded"] if _f])
    geo = load_geometry_from_h5(
        params["sim_file"], h5group, comm=patient.mesh.mpi_comm()
    )
    setattr(patient, "original_geometry", getattr(patient, "mesh"))

    for k, v in geo.__dict__.items():
        if hasattr(patient, k):
            delattr(patient, k)

        setattr(patient, k, v)

    return patient


def initialize_patient_data(patient_parameters):
    """
    Make an instance of patient from :py:module`patient_data`
    baed on th given parameters

    *Parameters*

    patient_parameters: dict
        the parameters 
    
    *Returns*

    patient: :py:class`patient_data.Patient`
        A patient instance

    **Example of usage**::
    
      params = setup_patient_parameters()
      patient = initialize_patient_data(params)

    """

    logger.info("Initialize patient data")
    from .patient_data import Patient

    patient = Patient(**patient_parameters)

    return patient


def check_patient_attributes(patient):
    """
    Check that the object contains the minimum 
    required attributes. 
    """

    msg = "Patient is missing attribute {}"

    # Mesh
    if not hasattr(patient, "mesh"):
        raise AttributeError(msg.format("mesh"))
    else:
        dim = patient.mesh.topology().dim()

    ## Microstructure

    # Fibers
    if not hasattr(patient, "fiber"):

        no_fiber = True
        if hasattr(patient, "e_f"):
            rename_attribute(patient, "e_f", "fiber")
            no_fiber = False

        if no_fiber:

            idx_arr = np.where([item.startswith("fiber") for item in dir(patient)])[0]
            if len(idx_arr) == 0:
                raise AttributeError(msg.format("fiber"))
            else:
                att = dir(patient)[idx_arr[0]]
                rename_attribute(patient, att, "fiber")

    # Sheets
    if not hasattr(patient, "sheet"):
        if hasattr(patient, "e_s"):
            rename_attribute(patient, "e_s", "sheet")
        else:
            setattr(patient, "sheet", None)

    # Cross-sheet
    if not hasattr(patient, "sheet_normal"):
        if hasattr(patient, "e_sn"):
            rename_attribute(patient, "e_sn", "sheet_normal")
        else:
            setattr(patient, "sheet_normal", None)

    ## Local basis

    # Circumferential
    if not hasattr(patient, "circumferential") and hasattr(patient, "e_circ"):
        rename_attribute(patient, "e_circ", "circumferential")

    # Radial
    if not hasattr(patient, "radial") and hasattr(patient, "e_rad"):
        rename_attribute(patient, "e_rad", "radial")

    # Longitudinal
    if not hasattr(patient, "longitudinal") and hasattr(patient, "e_long"):
        rename_attribute(patient, "e_long", "longitudinal")

    ## Markings

    # Markers
    if not hasattr(patient, "markers"):
        raise AttributeError(msg.format("markers"))

    # Facet fuction
    if not hasattr(patient, "ffun"):

        no_ffun = True
        if hasattr(patient, "facets_markers"):
            rename_attribute(patient, "facets_markers", "ffun")
            no_ffun = False

        if no_ffun:
            setattr(
                patient,
                "ffun",
                MeshFunction("size_t", patient.mesh, 2, patient.mesh.domains()),
            )

    # Cell markers
    if dim == 3 and not hasattr(patient, "sfun"):

        no_sfun = True
        if no_sfun and hasattr(patient, "strain_markers"):
            rename_attribute(patient, "strain_markers", "sfun")
            no_sfun = False

        if no_sfun:
            setattr(
                patient,
                "sfun",
                MeshFunction("size_t", patient.mesh, 3, patient.mesh.domains()),
            )

    ## Other

    # Weigts on strain semgements
    if not hasattr(patient, "strain_weights"):
        setattr(patient, "strain_weights", None)

    # Mesh type
    if not hasattr(patient, "mesh_type"):
        # If markers are according to fiberrules,
        # rv should be marked with 20
        if 20 in set(patient.ffun.array()):
            setattr(patient, "mesh_type", lambda: "biv")
        else:
            setattr(patient, "mesh_type", lambda: "lv")

    if not hasattr(patient, "passive_filling_duration"):
        setattr(patient, "passive_filling_duration", 1)


def save_patient_data_to_simfile(patient, sim_file):

    from mesh_generation.mesh_utils import save_geometry_to_h5

    fields = []
    for att in ["fiber", "sheet", "sheet_normal"]:
        if hasattr(patient, att):
            fields.append(getattr(patient, att))

    local_basis = []
    for att in ["circumferential", "radial", "longitudinal"]:
        if hasattr(patient, att):
            local_basis.append(getattr(patient, att))

    save_geometry_to_h5(
        patient.mesh, sim_file, "", patient.markers, fields, local_basis
    )


def get_simulated_strain_traces(phm):
    simulated_strains = {
        strain: np.zeros(17) for strain in list(STRAIN_NUM_TO_KEY.values())
    }
    strains = phm.strains
    for direction in range(3):
        for region in range(17):
            simulated_strains[STRAIN_NUM_TO_KEY[direction]][region] = gather_broadcast(
                strains[region].vector().array()
            )[direction]
    return simulated_strains


def make_solver_params(params, patient, measurements=None):

    paramvec, gamma, matparams = make_control(params, patient)
    return make_solver_parameters(
        params, patient, matparams, gamma, paramvec, measurements
    )


def make_solver_parameters(
    params, patient, matparams, gamma=Constant(0.0), paramvec=None, measurements=None
):

    ##  Material
    Material = get_material_model(params["material_model"])
    material = Material(
        f0=patient.fiber,
        activation=gamma,
        parameters=matparams,
        s0=patient.sheet,
        n0=patient.sheet_normal,
        **params
    )

    if measurements is None:
        p_lv_ = 0.0
        p_rv_ = 0.0
    else:
        p_lv_ = measurements["pressure"][0]
        if "rv_pressure" in measurements:
            p_rv_ = measurements["rv_pressure"][0]

    # Neumann BC
    neuman_bc = []

    p_lv = Constant(p_lv_, name="LV_endo_pressure")

    if "ENDO_LV" in patient.markers:

        p_rv = Constant(p_rv_, name="RV_endo_pressure")

        neumann_bc = [
            [p_lv, patient.markers["ENDO_LV"][0]],
            [p_rv, patient.markers["ENDO_RV"][0]],
        ]

        pressure = {"p_lv": p_lv, "p_rv": p_rv}
    else:
        neumann_bc = [[p_lv, patient.markers["ENDO"][0]]]
        pressure = {"p_lv": p_lv}

    pericard = Constant(params["pericardium_spring"])
    robin_bc = [[pericard, patient.markers["EPI"][0]]]

    if params["base_bc"] == "from_seg_base":

        # Direchlet BC at the Base
        try:
            mesh_verts = patient.mesh_verts
            seg_verts = measurements.seg_verts
        except:
            raise ValueError(
                (
                    "No mesh vertices found. Fix base "
                    + "is the only applicable Direchlet BC"
                )
            )

        endoring = VertexDomain(mesh_verts)
        base_it = Expression("t", t=0.0, name="base_iterator")

        # Expression for defining the boundary conditions
        base_bc_y = BaseExpression(
            mesh_verts, seg_verts, "y", base_it, name="base_expr_y"
        )
        base_bc_z = BaseExpression(
            mesh_verts, seg_verts, "z", base_it, name="base_expr_z"
        )

        def base_bc(W):
            """
            Fix base in the x = 0 plane, and fix the vertices at 
            the endoring at the base according to the segmeted surfaces. 
            """
            V = W if W.sub(0).num_sub_spaces() == 0 else W.sub(0)

            bc = [
                DirichletBC(
                    V.sub(0), Constant(0.0), patient.ffun, patient.markers["BASE"][0]
                ),
                DirichletBC(V.sub(1), base_bc_y, endoring, "pointwise"),
                DirichletBC(V.sub(2), base_bc_z, endoring, "pointwise"),
            ]
            return bc

    elif params["base_bc"] == "fixed":

        base_bc_y = None
        base_bc_z = None
        base_it = None

        def base_bc(W):
            """Fix the basal plane.
            """
            V = W if W.sub(0).num_sub_spaces() == 0 else W.sub(0)
            bc = [
                DirichletBC(
                    V, Constant((0, 0, 0)), patient.ffun, patient.markers["BASE"][0]
                )
            ]
            return bc

    else:

        if not (params["base_bc"] == "fix_x"):
            logger.warning("Unknown Base BC {}".format(params["base_bc"]))
            logger.warning("Fix base in x direction")

        def base_bc(W):
            """Make Dirichlet boundary conditions where the base is allowed to slide
            in the x = 0 plane.
            """
            V = W if W.sub(0).num_sub_spaces() == 0 else W.sub(0)
            bc = [DirichletBC(V.sub(0), 0, patient.ffun, patient.markers["BASE"][0])]
            return bc

        # Apply a linear sprint robin type BC to limit motion
        # base_spring = Function(V_real, name = "base_spring")
        base_spring = Constant(params["base_spring_k"])
        robin_bc += [[base_spring, patient.markers["BASE"][0]]]

    # Circumferential, Radial and Longitudinal basis vector
    crl_basis = {}
    for att in ["circumferential", "radial", "longitudinal"]:
        if hasattr(patient, att):
            crl_basis[att] = getattr(patient, att)

    solver_parameters = {
        "mesh": patient.mesh,
        "facet_function": patient.ffun,
        "facet_normal": FacetNormal(patient.mesh),
        "crl_basis": crl_basis,
        "mesh_function": patient.sfun,
        "markers": patient.markers,
        "passive_filling_duration": patient.passive_filling_duration,
        "strain_weights": patient.strain_weights,
        "state_space": "P_2:P_1",
        "compressibility": {
            "type": params["compressibility"],
            "lambda": params["incompressibility_penalty"],
        },
        "material": material,
        "bc": {"dirichlet": base_bc, "neumann": neumann_bc, "robin": robin_bc},
        "solve": setup_solver_parameters(),
    }

    if params["phase"] in [PHASES[0], PHASES[2]]:
        return solver_parameters, pressure, paramvec
    elif params["phase"] == PHASES[1]:
        return solver_parameters, pressure, gamma
    else:
        return solver_parameters, pressure


def make_control(params, patient):

    ##  Contraction parameter
    if params["gamma_space"] == "regional":
        sfun = merge_control(patient, params["merge_active_control"])
        gamma = RegionalParameter(sfun)
    else:
        gamma_family, gamma_degree = params["gamma_space"].split("_")
        gamma_space = FunctionSpace(patient.mesh, gamma_family, int(gamma_degree))

        gamma = Function(gamma_space, name="activation parameter")

    ##  Material parameters

    # Create an object for each single material parameter
    if params["matparams_space"] == "regional":
        sfun = merge_control(patient, params["merge_passive_control"])
        paramvec_ = RegionalParameter(sfun)

    else:

        family, degree = params["matparams_space"].split("_")
        matparams_space = FunctionSpace(patient.mesh, family, int(degree))
        paramvec_ = Function(matparams_space, name="matparam vector")

    # If we want to estimate more than one parameter

    # Number of passive parameters to optimize
    npassive = sum([not v for v in list(params["Fixed_parameters"].values())])

    if npassive <= 1:
        # If there is only one parameter, just pick the same object
        paramvec = paramvec_

        # If there is none then
        if npassive == 0:
            logger.debug("All material paramters are fixed")
            params["optimize_matparams"] = False

    else:

        # Otherwise, we make a mixed parameter
        paramvec = MixedParameter(paramvec_, npassive)
        # Make an iterator for the function assigment
        nopts_par = 0

    if params["phase"] in [PHASES[1]]:
        # Load the parameters from the result file

        # Open simulation file
        with HDF5File(mpi_comm_world(), params["sim_file"], "r") as h5file:

            # Get material parameter from passive phase file
            h5file.read(paramvec, PASSIVE_INFLATION_GROUP + "/optimal_control")

    matparams = params["Material_parameters"].to_dict()

    for par, val in matparams.items():

        # Check if material parameter should be fixed
        if not params["Fixed_parameters"][par]:
            # If not, then we need to put the parameter into some dolfin function

            # Use the materal parameters from the parameters as initial guess
            if params["phase"] in [PHASES[0], PHASES[2]]:

                val_const = (
                    Constant(val)
                    if paramvec_.value_size() == 1
                    else Constant([val] * paramvec_.value_size())
                )

                if npassive <= 1:
                    paramvec.assign(val_const)

                else:
                    paramvec.assign_sub(val_const, nopts_par)

            if npassive <= 1:
                matparams[par] = paramvec

            else:
                matparams[par] = split(paramvec)[nopts_par]
                nopts_par += 1

    # Print the material parameter to stdout
    logger.info("\nMaterial Parameters")
    nopts_par = 0

    for par, v in matparams.items():
        if isinstance(v, (float, int)):
            logger.info("\t{}\t= {:.3f}".format(par, v))
        else:

            if npassive <= 1:
                v_ = gather_broadcast(v.vector().array())

            else:
                v_ = gather_broadcast(
                    paramvec.split(deepcopy=True)[nopts_par].vector().array()
                )
                nopts_par += 1

            sp_str = "(mean), spatially resolved" if len(v_) > 1 else ""
            logger.info("\t{}\t= {:.3f} {}".format(par, v_.mean(), sp_str))

    return paramvec, gamma, matparams


def get_measurements(params, patient):
    """Get the measurement to be used as BC 
    or targets in the optimization

    :param params: Application parameter
    :param patient: class with the patient data
    :returns: The target data
    :rtype: dict

    """

    # Parameters for the targets
    p = params["Optimization_targets"]
    measurements = {}

    # Find the start and end of the measurements
    if params["phase"] == PHASES[0]:  # Passive inflation
        # We need just the points from the passive phase
        start = 0
        end = patient.passive_filling_duration

        pvals = params["Passive_optimization_weigths"]

    elif params["phase"] == PHASES[1]:
        # We need just the points from the active phase
        start = patient.passive_filling_duration - 1
        end = patient.num_points

        pvals = params["Active_optimization_weigths"]

        if params["unload"]:
            start += 1

    else:
        # We need all the points
        start = 0
        end = patient.num_points

        # pvals = params["Passive_optimization_weigths"]
        pvals = params["Active_optimization_weigths"]

    if params["unload"]:
        end += 1

    p["volume"] = pvals["volume"] > 0 or params["phase"] == "all"
    p["rv_volume"] = hasattr(patient, "RVV") and (
        pvals["rv_volume"] > 0 or params["phase"] == "all"
    )

    p["regional_strain"] = hasattr(patient, "strain") and (
        pvals["regional_strain"] > 0 or params["phase"] == "all"
    )

    ## Pressure

    # We need the pressure as a BC
    pressure = np.array(patient.pressure)

    # Compute offsets
    # Choose the pressure at the beginning as reference pressure
    if params["unload"]:
        reference_pressure = 0.0
        pressure = np.append(0.0, pressure)
    else:
        reference_pressure = pressure[0]
    logger.info("LV Pressure offset = {} kPa".format(reference_pressure))

    # Here the issue is that we do not have a stress free reference mesh.
    # The reference mesh we use is already loaded with a certain
    # amount of pressure, which we remove.
    pressure = np.subtract(pressure, reference_pressure)
    measurements["pressure"] = pressure[start:end]

    if hasattr(patient, "RVP"):
        rv_pressure = np.array(patient.RVP)
        if params["unload"]:
            reference_pressure = 0.0
            rv_pressure = np.append(0.0, rv_pressure)
        else:
            reference_pressure = rv_pressure[0]
        logger.info("RV Pressure offset = {} kPa".format(reference_pressure))

        rv_pressure = np.subtract(rv_pressure, reference_pressure)
        measurements["rv_pressure"] = rv_pressure[start:end]

    ## Volume
    if p["volume"]:
        # Calculate difference bwtween calculated volume, and volume given from echo
        volume_offset = get_volume_offset(patient, params)
        logger.info("LV Volume offset = {} cm3".format(volume_offset))
        logger.info("Measured LV volume = {}".format(patient.volume[0]))

        # Subtract this offset from the volume data
        volume = np.subtract(patient.volume, volume_offset)
        logger.info("Computed LV volume = {}".format(volume[0]))
        if params["unload"]:
            volume = np.append(-1, volume)

        measurements["volume"] = volume[start:end]

    if p["rv_volume"]:
        # Calculate difference bwtween calculated volume, and volume given from echo
        volume_offset = get_volume_offset(patient, params, "rv")
        logger.info("RV Volume offset = {} cm3".format(volume_offset))
        logger.info("Measured RV volume = {}".format(patient.RVV[0]))

        # Subtract this offset from the volume data
        volume = np.subtract(patient.RVV, volume_offset)
        logger.info("Computed RV volume = {}".format(volume[0]))
        if params["unload"]:
            volume = np.append(-1, volume)

        measurements["rv_volume"] = volume[start:end]

    if p["regional_strain"]:

        strain = {}
        if hasattr(patient, "strain"):
            for region in list(patient.strain.keys()):

                s = patient.strain[region]
                if params["unload"]:
                    s = [(0.0, 0.0, 0.0)] + s

                strain[region] = s[start:end]

        else:
            msg = (
                "\nPatient do not have strain as attribute."
                + "\nStrain will not be used"
            )
            p["regional_strain"] = False
            logger.warning(msg)

        measurements["regional_strain"] = strain

    return measurements


def get_volume(patient, unload=False, chamber="lv", u=None):

    if unload:
        mesh = patient.original_geometry
        ffun = MeshFunction("size_t", mesh, 2, mesh.domains())
    else:
        mesh = patient.mesh
        ffun = patient.ffun

    if chamber == "lv":
        if "ENDO_LV" in patient.markers:
            endo_marker = patient.markers["ENDO_LV"][0]
        else:
            endo_marker = patient.markers["ENDO"][0]

    else:
        endo_marker = patient.markers["ENDO_RV"][0]

    dS = Measure("exterior_facet", subdomain_data=ffun, domain=mesh)(endo_marker)

    X = SpatialCoordinate(mesh)
    N = FacetNormal(mesh)
    if u is None:
        vol_form = (-1.0 / 3.0) * dot(X, N)
    else:
        F = grad(u) + Identity(3)
        J = det(F)
        vol_form = (-1.0 / 3.0) * dot(X + u, J * inv(F).T * N)

    vol = assemble(vol_form * ds)
    return vol


def get_volume_offset(patient, params, chamber="lv"):

    if params["Patient_parameters"]["geometry_index"] == "-1":
        idx = patient.passive_filling_duration - 1
    else:
        idx = int(params["Patient_parameters"]["geometry_index"])

    if chamber == "lv":
        volume = patient.volume[idx]
    else:
        volume = patient.RVV[idx]

    logger.info("Measured = {}".format(volume))
    vol = get_volume(patient, params["unload"], chamber)
    return volume - vol


def setup_simulation(params, patient):

    check_patient_attributes(patient)
    # Load measurements
    measurements = get_measurements(params, patient)
    solver_parameters, pressure, controls = make_solver_params(
        params, patient, measurements
    )

    return measurements, solver_parameters, pressure, controls


class MyReducedFunctional(ReducedFunctional):
    """
    A modified reduced functional of the `dolfin_adjoint.ReducedFuctionl`

    *Parameters*
    
    for_run: callable
        The forward model, which can be called with the control parameter
        as first argument, and a boolean as second, indicating that annotation is on/off.
    paramvec: :py:class`dolfin_adjoint.function`
        The control parameter
    scale: float
        Scale factor for the functional
    relax: float
        Scale factor for the derivative. Note the total scale factor for the 
        derivative will be scale*relax


    """

    def __init__(self, for_run, paramvec, scale=1.0, relax=1.0, verbose=False):

        self.log_level = logger.level
        self.reset()
        self.for_run = for_run
        self.paramvec = paramvec

        self.initial_paramvec = gather_broadcast(paramvec.vector().array())
        self.scale = scale
        self.derivative_scale = relax

        self.verbose = verbose
        from .optimal_control import has_scipy016

    def __call__(self, value, return_fail=False):

        logger.debug("\nEvaluate functional...")
        adj_reset()
        self.iter += 1

        paramvec_new = Function(self.paramvec.function_space(), name="new control")

        if isinstance(value, (Function, RegionalParameter, MixedParameter)):
            paramvec_new.assign(value)
        elif isinstance(value, float) or isinstance(value, int):
            assign_to_vector(paramvec_new.vector(), np.array([value]))
        elif isinstance(value, enlisting.Enlisted):
            val_delisted = delist(value, self.controls)
            paramvec_new.assign(val_delisted)

        else:
            assign_to_vector(paramvec_new.vector(), gather_broadcast(value))

        logger.debug(Text.yellow("Start annotating"))
        parameters["adjoint"]["stop_annotating"] = False

        if self.verbose:
            arr = gather_broadcast(paramvec_new.vector().array())
            msg = (
                "\nCurrent value of control:"
                + "\n\t{:>8}\t{:>8}\t{:>8}\t{:>8}\t{:>8}".format(
                    "Min", "Mean", "Max", "argmin", "argmax"
                )
                + "\n\t{:>8.2f}\t{:>8.2f}\t{:>8.2f}\t{:>8d}\t{:>8d}".format(
                    np.min(arr),
                    np.mean(arr),
                    np.max(arr),
                    np.argmin(arr),
                    np.argmax(arr),
                )
            )
            logger.info(msg)
        # Change loglevel to avoid to much printing (do not change if in dbug mode)
        change_log_level = (self.log_level == logging.INFO) and not self.verbose

        if change_log_level:
            logger.setLevel(WARNING)

        t = Timer("Forward run")
        t.start()

        logger.debug("\nEvaluate forward model")

        self.for_res, crash = self.for_run(paramvec_new, True)

        for_time = t.stop()
        logger.debug(
            (
                "Evaluating forward model done. "
                + "Time to evaluate = {} seconds".format(for_time)
            )
        )
        self.forward_times.append(for_time)

        if change_log_level:
            logger.setLevel(self.log_level)

        if self.first_call:
            # Store initial results
            self.ini_for_res = self.for_res
            self.first_call = False

            # Some printing
            logger.info(print_head(self.for_res))

        control = Control(self.paramvec)

        ReducedFunctional.__init__(
            self, Functional(self.for_res["total_functional"]), control
        )

        if crash:
            # This exection is thrown if the solver uses more than x steps.
            # The solver is stuck, return a large value so it does not get stuck again
            logger.warning(
                Text.red(
                    "Iteration limit exceeded. Return a large value of the functional"
                )
            )
            # Return a big value, and make sure to increment the big value so the
            # the next big value is different from the current one.
            func_value = np.inf
            self.nr_crashes += 1

        else:
            func_value = self.for_res["func_value"]

        grad_norm = (
            None if len(self.grad_norm_scaled) == 0 else self.grad_norm_scaled[-1]
        )

        self.func_values_lst.append(func_value * self.scale)
        self.controls_lst.append(Vector(paramvec_new.vector()))

        logger.debug(Text.yellow("Stop annotating"))
        parameters["adjoint"]["stop_annotating"] = True

        self.print_line()

        if return_fail:
            return self.scale * func_value, crash

        return self.scale * func_value

    def reset(self):

        logger.setLevel(self.log_level)
        if not hasattr(self, "ini_for_res"):

            self.cache = None
            self.first_call = True
            self.nr_crashes = 0
            self.iter = 0
            self.nr_der_calls = 0
            self.func_values_lst = []
            self.controls_lst = []
            self.forward_times = []
            self.backward_times = []
            self.grad_norm = []
            self.grad_norm_scaled = []
        else:
            if len(self.func_values_lst):
                self.func_values_lst.pop()
            if len(self.controls_lst):
                self.controls_lst.pop()
            if len(self.grad_norm):
                self.grad_norm.pop()
            if len(self.grad_norm_scaled):
                self.grad_norm_scaled.pop()

    def print_line(self):
        grad_norm = (
            None if len(self.grad_norm_scaled) == 0 else self.grad_norm_scaled[-1]
        )

        func_value = self.for_res["func_value"]

        logger.info(print_line(self.for_res, self.iter, grad_norm, func_value))

    def derivative(self, *args, **kwargs):

        logger.debug("\nEvaluate gradient...")
        self.nr_der_calls += 1
        import math

        t = Timer("Backward run")
        t.start()

        out = ReducedFunctional.derivative(self, forget=False)
        back_time = t.stop()
        logger.debug(
            (
                "Evaluating gradient done. "
                + "Time to evaluate = {} seconds".format(back_time)
            )
        )
        self.backward_times.append(back_time)

        for num in out[0].vector().array():
            if math.isnan(num):
                raise Exception("NaN in adjoint gradient calculation.")

        # Multiply with some small number to that we take smaller steps
        gathered_out = gather_broadcast(out[0].vector().array())

        self.grad_norm.append(np.linalg.norm(gathered_out))
        self.grad_norm_scaled.append(
            np.linalg.norm(gathered_out) * self.scale * self.derivative_scale
        )
        logger.debug(
            "|dJ|(actual) = {}\t|dJ|(scaled) = {}".format(
                self.grad_norm[-1], self.grad_norm_scaled[-1]
            )
        )
        return self.scale * gathered_out * self.derivative_scale


class RegionalParameter(dolfin.Function):
    def __init__(self, meshfunction):

        assert isinstance(
            meshfunction, MeshFunctionSizet
        ), "Invalid meshfunction for regional gamma"

        mesh = meshfunction.mesh()

        self._values = set(gather_broadcast(meshfunction.array()))
        self._nvalues = len(self._values)

        V = dolfin.VectorFunctionSpace(mesh, "R", 0, dim=self._nvalues)

        dolfin.Function.__init__(self, V)
        self._meshfunction = meshfunction

        # Functionspace for the indicator functions
        self._IndSpace = dolfin.FunctionSpace(mesh, "DG", 0)

        # Make indicator functions
        self._ind_functions = []
        for v in self._values:
            self._ind_functions.append(self._make_indicator_function(v))

    def get_ind_space(self):
        return self._IndSpace

    def get_values(self):
        return self._values

    def get_function(self):
        """
        Return linear combination of coefficents
        and basis functions

        :returns: A function with parameter values at each segment
                  specified by the meshfunction
        :rtype:  :py:class`dolfin.Function             
             
        """
        return self._sum()

    def _make_indicator_function(self, marker):
        dm = self._IndSpace.dofmap()
        cell_dofs = [
            dm.cell_dofs(i) for i in np.where(self._meshfunction.array() == marker)[0]
        ]
        dofs = np.unique(np.array(cell_dofs))

        f = dolfin.Function(self._IndSpace)
        f.vector()[dofs] = 1.0
        return f

    def _sum(self):
        coeffs = dolfin.split(self)
        fun = coeffs[0] * self._ind_functions[0]

        for c, f in zip(coeffs[1:], self._ind_functions[1:]):
            fun += c * f

        return fun


class MixedParameter(dolfin.Function):
    def __init__(self, fun, n, name="material_parameters"):
        """
        Initialize Mixed parameter.

        This will instanciate a function in a dolfin.MixedFunctionSpace
        consiting of `n` subspaces of the same type as `fun`.
        This is of course easy for the case when `fun` is a normal
        dolfin function, but in the case of a `RegionalParameter` it
        is not that straight forward. 
        This class handles this case as well. 

        

        :param fun: The type of you want to make a du
        :type fun: (:py:class:`dolfin.Function`)
        :param int n: number of subspaces 
        :param str name: Name of the function

        .. todo::
        
           Implement support for MixedParameter with different
           types of subspaces, e.g [RegionalParamter, R_0, CG_1]

        """

        msg = "Please provide a dolin function as argument to MixedParameter"
        assert isinstance(fun, (dolfin.Function, RegionalParameter)), msg

        if isinstance(fun, RegionalParameter):
            raise NotImplementedError

        # We can just make a usual mixed function space
        # with n copies of the original one
        V = fun.function_space()
        W = dolfin.MixedFunctionSpace([V] * n)

        dolfin.Function.__init__(self, W, name=name)

        # Create a function assigner
        self.function_assigner = [
            dolfin.FunctionAssigner(W.sub(i), V) for i in range(n)
        ]

        # Store the original function space
        self.basespace = V

        if isinstance(fun, RegionalParameter):
            self._meshfunction = fun._meshfunction

    def assign_sub(self, f, i):
        """
        Assign subfunction

        :param f: The function you want to assign
        :param int i: The subspace number

        """
        f_ = Function(self.basespace)
        f_.assign(f)
        self.function_assigner[i].assign(self.split()[i], f_)


class BaseExpression(Expression):
    """
    A class for assigning boundary condition according to segmented surfaces
    Since the base is located at x = a (usually a=0), two classes must be set: 
    One for the y-direction and one for the z-direction

    Point on the endocardium and epicardium is given and the
    points on the mesh base is set accordingly.
    Points that lie on the base but not on the epi- or endoring
    will be given a zero value.
    """

    def __init__(self, mesh_verts, seg_verts, sub, it, name):
        """
        
        *Arguments*
          mesh: (dolfin.mesh)
            The mesh

          u: (dolfin.GenericFunction)
            Initial displacement

          mesh_verts (numpy.ndarray or list)
            Point of endocardial base from mesh

          seg_verts (numpy.ndarray or list)
            Point of endocardial base from segmentation

          sub (str)
            Either "y" or "z". The displacement in this direction is returned

          it (dolfin.Expression)
            Can be used to incrment the direclet bc

        """
        assert sub in ["y", "z"]
        self._mesh_verts = np.array(mesh_verts)
        self._all_seg_verts = np.array(seg_verts)
        self.point = 0
        self.npoints = len(seg_verts) - 1

        self._seg_verts = self._all_seg_verts[0]

        self._sub = sub
        self._it = it
        self.rename(name, name)

    def __next__(self):
        self._it.t = 0
        self.point += 1
        self._seg_verts = self._all_seg_verts[self.point]

    def reset(self):
        self.point = 0
        self._it.t = 0

    def eval(self, value, x):

        # Check if given coordinate is in the endoring vertices
        # and find the cooresponding index
        d = [np.where(x[i] == self._mesh_verts.T[i])[0] for i in range(3)]
        d_intersect = set.intersection(*list(map(set, d)))
        assert len(d_intersect) < 2
        if len(d_intersect) == 1:

            idx = d_intersect.pop()

            prev_seg_verts = self._all_seg_verts[self.point - 1]

            # Return the displacement in the given direction
            # Iterated starting from the previous displacemet to the current one
            if self._sub == "y":
                u_prev = self._mesh_verts[idx][1] - prev_seg_verts[idx][1]
                u_current = self._mesh_verts[idx][1] - self._seg_verts[idx][1]
                # value[0] = u_prev + self._it.t*(u_current - u_prev)
            else:  # sub == "z"
                u_prev = self._mesh_verts[idx][2] - prev_seg_verts[idx][2]
                u_current = self._mesh_verts[idx][2] - self._seg_verts[idx][2]

            val = u_prev + self._it.t * (u_current - u_prev)
            value[0] = val

        else:
            value[0] = 0


class VertexDomain(SubDomain):
    """
    A subdomain defined in terms of
    a given set of coordinates.
    A point that is close to the given coordinates
    within a given tolerance will be marked as inside 
    the domain.
    """

    def __init__(self, coords, tol=1e-4):
        """
        *Arguments*
          coords (list)
            List of coordinates for vertices in reference geometry
            defining this domains

          tol (float)
            Tolerance for how close a pointa should be to the given coordinates
            to be marked as inside the domain
        """
        self.coords = np.array(coords)
        self.tol = tol
        SubDomain.__init__(self)

    def inside(self, x, on_boundary):

        if np.all([np.any(abs(x[i] - self.coords.T[i]) < self.tol) for i in range(3)]):
            return True

        return False
