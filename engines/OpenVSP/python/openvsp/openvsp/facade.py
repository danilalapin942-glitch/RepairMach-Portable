
# Facade Code
# **********************************************************************************
import os
import sys
import socket
from time import sleep, time
import subprocess
import pickle
from openvsp.facade_server import pack_data, unpack_data
from traceback import format_exception
import openvsp_config
# Import the low-level C/C++ module
if __package__ or "." in __name__:
    from . import _vsp
else:
    import _vsp
# decorator for wrapping every function
def client_wrap(func):
    def wrapper(self, *args, **kwargs):
        return self._send_receive(func.__name__, args, kwargs)
    return wrapper
def _exception_hook(exc_type, exc_value, tb):
    regular_traceback = []
    facade_traceback = []
    for line in exc_value.args[0].split("\n")[:3]:
        facade_traceback.append(line)

    for line in format_exception(exc_type, exc_value, tb)[:-1]:
        if "facade.py" in line or "facade_server.py" in line:
            facade_traceback.append(line.strip("\n"))
        else:
            regular_traceback.append(line.strip("\n"))
    for line in exc_value.args[0].split("\n")[3:]:
        regular_traceback.append(line)

    print("This error occurred while using the facade API")
    print("Facade Traceback:")
    for line in facade_traceback:
        print(line)
    print("")
    print("Regular Traceback:")
    for line in regular_traceback:
        print(line)

class _vsp_server():
    def __init__(self, name, funcs=[], port=-1):
        self.server_name = name
        HOST = 'localhost'
        if port>0:
            self.port = port
        else:
            self.port = 0
        python_exe = None
        if "python" in os.path.basename(sys.executable):
            python_exe = sys.executable
        elif "python" in os.path.basename(os.__file__):
            python_exe = os.__file__
        else:
            python_exe = "python"
        server_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'facade_server.py')
        for i in range(openvsp_config.FACADE_SERVER_ATTEMPTS):
            try:
                self._proc = subprocess.Popen([python_exe, server_file, str(self.port), str(openvsp_config.LOAD_GRAPHICS), str(openvsp_config.FACADE_PRINT_LEVEL)], stderr=subprocess.PIPE)
                start_time = time()
                timeout = openvsp_config.FACADE_SERVER_TIMEOUT  # seconds
                while True:
                    line = self._proc.stderr.readline().strip().decode()
                    # print(line)
                    if not line:
                        if self._proc.poll() is not None:
                            raise RuntimeError("Server process exited unexpectedly")
                        if time() - start_time > timeout:
                            raise TimeoutError("Waiting for server timed out")
                        continue  # No line, but process still alive wait more

                    if line.startswith("OpenVSP Server Socket Thread: Bound to"):
                        self.port = int(line.split()[7][:-2])
                        break
                break
            except Exception as e:
                if openvsp_config.FACADE_PRINT_LEVEL > 0: print(f'OpenVSP failed to start server, attempt {i+1}, trying again; "{str(e)}"')
                try:
                    self._proc.close()
                    self._proc = None
                except:
                    pass
        if self._proc is None:
            raise RuntimeError("Facade failed to start the server")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect((HOST, self.port))

        for func in funcs:
            setattr(self, func.__name__, (lambda  *args, func=func, **kwargs: self._run_func(func, *args, **kwargs)))

        self.ABS = _vsp.ABS
        self.REL = _vsp.REL
        self.SELIG_AF_EXPORT = _vsp.SELIG_AF_EXPORT
        self.BEZIER_AF_EXPORT = _vsp.BEZIER_AF_EXPORT
        self.ALIGN_LEFT = _vsp.ALIGN_LEFT
        self.ALIGN_CENTER = _vsp.ALIGN_CENTER
        self.ALIGN_RIGHT = _vsp.ALIGN_RIGHT
        self.ALIGN_PIXEL = _vsp.ALIGN_PIXEL
        self.ALIGN_TOP = _vsp.ALIGN_TOP
        self.ALIGN_MIDDLE = _vsp.ALIGN_MIDDLE
        self.ALIGN_BOTTOM = _vsp.ALIGN_BOTTOM
        self.NUM_ALIGN_TYPE = _vsp.NUM_ALIGN_TYPE
        self.ANG_RAD = _vsp.ANG_RAD
        self.ANG_DEG = _vsp.ANG_DEG
        self.ANG_0 = _vsp.ANG_0
        self.ANG_90 = _vsp.ANG_90
        self.ANG_180 = _vsp.ANG_180
        self.ANG_270 = _vsp.ANG_270
        self.NUM_ANG = _vsp.NUM_ANG
        self.ATMOS_TYPE_US_STANDARD_1976 = _vsp.ATMOS_TYPE_US_STANDARD_1976
        self.ATMOS_TYPE_HERRINGTON_1966 = _vsp.ATMOS_TYPE_HERRINGTON_1966
        self.ATMOS_TYPE_MANUAL_P_R = _vsp.ATMOS_TYPE_MANUAL_P_R
        self.ATMOS_TYPE_MANUAL_P_T = _vsp.ATMOS_TYPE_MANUAL_P_T
        self.ATMOS_TYPE_MANUAL_R_T = _vsp.ATMOS_TYPE_MANUAL_R_T
        self.ATMOS_TYPE_MANUAL_RE_L = _vsp.ATMOS_TYPE_MANUAL_RE_L
        self.ATTACH_TRANS_NONE = _vsp.ATTACH_TRANS_NONE
        self.ATTACH_TRANS_COMP = _vsp.ATTACH_TRANS_COMP
        self.ATTACH_TRANS_UV = _vsp.ATTACH_TRANS_UV
        self.ATTACH_TRANS_RST = _vsp.ATTACH_TRANS_RST
        self.ATTACH_TRANS_LMN = _vsp.ATTACH_TRANS_LMN
        self.ATTACH_TRANS_EtaMN = _vsp.ATTACH_TRANS_EtaMN
        self.ATTACH_TRANS_NUM_TYPES = _vsp.ATTACH_TRANS_NUM_TYPES
        self.ATTACH_ROT_NONE = _vsp.ATTACH_ROT_NONE
        self.ATTACH_ROT_COMP = _vsp.ATTACH_ROT_COMP
        self.ATTACH_ROT_UV = _vsp.ATTACH_ROT_UV
        self.ATTACH_ROT_RST = _vsp.ATTACH_ROT_RST
        self.ATTACH_ROT_LMN = _vsp.ATTACH_ROT_LMN
        self.ATTACH_ROT_EtaMN = _vsp.ATTACH_ROT_EtaMN
        self.ATTACH_ROT_NUM_TYPES = _vsp.ATTACH_ROT_NUM_TYPES
        self.ATTROBJ_PARM = _vsp.ATTROBJ_PARM
        self.ATTROBJ_GEOM = _vsp.ATTROBJ_GEOM
        self.ATTROBJ_VEH = _vsp.ATTROBJ_VEH
        self.ATTROBJ_SUBSURF = _vsp.ATTROBJ_SUBSURF
        self.ATTROBJ_MEASURE = _vsp.ATTROBJ_MEASURE
        self.ATTROBJ_LINK = _vsp.ATTROBJ_LINK
        self.ATTROBJ_ADVLINK = _vsp.ATTROBJ_ADVLINK
        self.ATTROBJ_ATTR = _vsp.ATTROBJ_ATTR
        self.ATTROBJ_COLLECTION = _vsp.ATTROBJ_COLLECTION
        self.ATTROBJ_XSEC = _vsp.ATTROBJ_XSEC
        self.ATTROBJ_SEC = _vsp.ATTROBJ_SEC
        self.ATTROBJ_MODE = _vsp.ATTROBJ_MODE
        self.ATTROBJ_SET = _vsp.ATTROBJ_SET
        self.ATTROBJ_VARGROUP = _vsp.ATTROBJ_VARGROUP
        self.ATTROBJ_VARSETTING = _vsp.ATTROBJ_VARSETTING
        self.ATTROBJ_FREE = _vsp.ATTROBJ_FREE
        self.ATTR_GROUP_NONE = _vsp.ATTR_GROUP_NONE
        self.ATTR_GROUP_WATERMARK = _vsp.ATTR_GROUP_WATERMARK
        self.NUM_ATTR_EVENT_GROUPS = _vsp.NUM_ATTR_EVENT_GROUPS
        self.AUX_GEOM_ROTOR_TIP_PATH = _vsp.AUX_GEOM_ROTOR_TIP_PATH
        self.AUX_GEOM_ROTOR_BURST = _vsp.AUX_GEOM_ROTOR_BURST
        self.AUX_GEOM_THREE_PT_GROUND = _vsp.AUX_GEOM_THREE_PT_GROUND
        self.AUX_GEOM_TWO_PT_GROUND = _vsp.AUX_GEOM_TWO_PT_GROUND
        self.AUX_GEOM_ONE_PT_GROUND = _vsp.AUX_GEOM_ONE_PT_GROUND
        self.AUX_GEOM_THREE_PT_CCE = _vsp.AUX_GEOM_THREE_PT_CCE
        self.AUX_GEOM_SUPER_CONE = _vsp.AUX_GEOM_SUPER_CONE
        self.AUX_GEOM_SINGLE_GEAR = _vsp.AUX_GEOM_SINGLE_GEAR
        self.AUX_GEOM_ROTOR_FRAGMENT = _vsp.AUX_GEOM_ROTOR_FRAGMENT
        self.AUX_GEOM_THROWN_BLADE = _vsp.AUX_GEOM_THROWN_BLADE
        self.AUX_GEOM_TIRE_SPRAY = _vsp.AUX_GEOM_TIRE_SPRAY
        self.AUX_GEOM_WHEEL_TIRE_FAILURE = _vsp.AUX_GEOM_WHEEL_TIRE_FAILURE
        self.NUM_AUX_GEOM_MODES = _vsp.NUM_AUX_GEOM_MODES
        self.BOGIE_CENTER_DIST = _vsp.BOGIE_CENTER_DIST
        self.BOGIE_CENTER_DIST_FRAC = _vsp.BOGIE_CENTER_DIST_FRAC
        self.BOGIE_GAP = _vsp.BOGIE_GAP
        self.BOGIE_GAP_FRAC = _vsp.BOGIE_GAP_FRAC
        self.NUM_BOGIE_SPACING_TYPE = _vsp.NUM_BOGIE_SPACING_TYPE
        self.BOR_FLOWTHROUGH = _vsp.BOR_FLOWTHROUGH
        self.BOR_UPPER = _vsp.BOR_UPPER
        self.BOR_LOWER = _vsp.BOR_LOWER
        self.BOR_NUM_MODES = _vsp.BOR_NUM_MODES
        self.MAX_CAMB = _vsp.MAX_CAMB
        self.DESIGN_CL = _vsp.DESIGN_CL
        self.CAM_TOP = _vsp.CAM_TOP
        self.CAM_FRONT = _vsp.CAM_FRONT
        self.CAM_FRONT_YUP = _vsp.CAM_FRONT_YUP
        self.CAM_LEFT = _vsp.CAM_LEFT
        self.CAM_LEFT_ISO = _vsp.CAM_LEFT_ISO
        self.CAM_BOTTOM = _vsp.CAM_BOTTOM
        self.CAM_REAR = _vsp.CAM_REAR
        self.CAM_RIGHT = _vsp.CAM_RIGHT
        self.CAM_RIGHT_ISO = _vsp.CAM_RIGHT_ISO
        self.CAM_CENTER = _vsp.CAM_CENTER
        self.NO_END_CAP = _vsp.NO_END_CAP
        self.FLAT_END_CAP = _vsp.FLAT_END_CAP
        self.ROUND_END_CAP = _vsp.ROUND_END_CAP
        self.EDGE_END_CAP = _vsp.EDGE_END_CAP
        self.SHARP_END_CAP = _vsp.SHARP_END_CAP
        self.POINT_END_CAP = _vsp.POINT_END_CAP
        self.ROUND_EXT_END_CAP_NONE = _vsp.ROUND_EXT_END_CAP_NONE
        self.ROUND_EXT_END_CAP_LE = _vsp.ROUND_EXT_END_CAP_LE
        self.ROUND_EXT_END_CAP_TE = _vsp.ROUND_EXT_END_CAP_TE
        self.ROUND_EXT_END_CAP_BOTH = _vsp.ROUND_EXT_END_CAP_BOTH
        self.NUM_END_CAP_OPTIONS = _vsp.NUM_END_CAP_OPTIONS
        self.CFD_MIN_EDGE_LEN = _vsp.CFD_MIN_EDGE_LEN
        self.CFD_MAX_EDGE_LEN = _vsp.CFD_MAX_EDGE_LEN
        self.CFD_MAX_GAP = _vsp.CFD_MAX_GAP
        self.CFD_NUM_CIRCLE_SEGS = _vsp.CFD_NUM_CIRCLE_SEGS
        self.CFD_GROWTH_RATIO = _vsp.CFD_GROWTH_RATIO
        self.CFD_LIMIT_GROWTH_FLAG = _vsp.CFD_LIMIT_GROWTH_FLAG
        self.CFD_INTERSECT_SUBSURFACE_FLAG = _vsp.CFD_INTERSECT_SUBSURFACE_FLAG
        self.CFD_HALF_MESH_FLAG = _vsp.CFD_HALF_MESH_FLAG
        self.CFD_FAR_FIELD_FLAG = _vsp.CFD_FAR_FIELD_FLAG
        self.CFD_FAR_MAX_EDGE_LEN = _vsp.CFD_FAR_MAX_EDGE_LEN
        self.CFD_FAR_MAX_GAP = _vsp.CFD_FAR_MAX_GAP
        self.CFD_FAR_NUM_CIRCLE_SEGS = _vsp.CFD_FAR_NUM_CIRCLE_SEGS
        self.CFD_FAR_SIZE_ABS_FLAG = _vsp.CFD_FAR_SIZE_ABS_FLAG
        self.CFD_FAR_LENGTH = _vsp.CFD_FAR_LENGTH
        self.CFD_FAR_WIDTH = _vsp.CFD_FAR_WIDTH
        self.CFD_FAR_HEIGHT = _vsp.CFD_FAR_HEIGHT
        self.CFD_FAR_X_SCALE = _vsp.CFD_FAR_X_SCALE
        self.CFD_FAR_Y_SCALE = _vsp.CFD_FAR_Y_SCALE
        self.CFD_FAR_Z_SCALE = _vsp.CFD_FAR_Z_SCALE
        self.CFD_FAR_LOC_MAN_FLAG = _vsp.CFD_FAR_LOC_MAN_FLAG
        self.CFD_FAR_LOC_X = _vsp.CFD_FAR_LOC_X
        self.CFD_FAR_LOC_Y = _vsp.CFD_FAR_LOC_Y
        self.CFD_FAR_LOC_Z = _vsp.CFD_FAR_LOC_Z
        self.CFD_SRF_XYZ_FLAG = _vsp.CFD_SRF_XYZ_FLAG
        self.CFD_STL_FILE_NAME = _vsp.CFD_STL_FILE_NAME
        self.CFD_POLY_FILE_NAME = _vsp.CFD_POLY_FILE_NAME
        self.CFD_TRI_FILE_NAME = _vsp.CFD_TRI_FILE_NAME
        self.CFD_OBJ_FILE_NAME = _vsp.CFD_OBJ_FILE_NAME
        self.CFD_DAT_FILE_NAME = _vsp.CFD_DAT_FILE_NAME
        self.CFD_KEY_FILE_NAME = _vsp.CFD_KEY_FILE_NAME
        self.CFD_GMSH_FILE_NAME = _vsp.CFD_GMSH_FILE_NAME
        self.CFD_TKEY_FILE_NAME = _vsp.CFD_TKEY_FILE_NAME
        self.CFD_FACET_FILE_NAME = _vsp.CFD_FACET_FILE_NAME
        self.CFD_VSPGEOM_FILE_NAME = _vsp.CFD_VSPGEOM_FILE_NAME
        self.CFD_NUM_FILE_NAMES = _vsp.CFD_NUM_FILE_NAMES
        self.POINT_SOURCE = _vsp.POINT_SOURCE
        self.LINE_SOURCE = _vsp.LINE_SOURCE
        self.BOX_SOURCE = _vsp.BOX_SOURCE
        self.ULINE_SOURCE = _vsp.ULINE_SOURCE
        self.WLINE_SOURCE = _vsp.WLINE_SOURCE
        self.NUM_SOURCE_TYPES = _vsp.NUM_SOURCE_TYPES
        self.TAG = _vsp.TAG
        self.REASON = _vsp.REASON
        self.CF_LAM_BLASIUS = _vsp.CF_LAM_BLASIUS
        self.CF_LAM_BLASIUS_W_HEAT = _vsp.CF_LAM_BLASIUS_W_HEAT
        self.CF_TURB_EXPLICIT_FIT_SPALDING = _vsp.CF_TURB_EXPLICIT_FIT_SPALDING
        self.CF_TURB_EXPLICIT_FIT_SPALDING_CHI = _vsp.CF_TURB_EXPLICIT_FIT_SPALDING_CHI
        self.CF_TURB_EXPLICIT_FIT_SCHOENHERR = _vsp.CF_TURB_EXPLICIT_FIT_SCHOENHERR
        self.DO_NOT_USE_CF_TURB_IMPLICIT_KARMAN = _vsp.DO_NOT_USE_CF_TURB_IMPLICIT_KARMAN
        self.CF_TURB_IMPLICIT_SCHOENHERR = _vsp.CF_TURB_IMPLICIT_SCHOENHERR
        self.CF_TURB_IMPLICIT_KARMAN_SCHOENHERR = _vsp.CF_TURB_IMPLICIT_KARMAN_SCHOENHERR
        self.CF_TURB_POWER_LAW_BLASIUS = _vsp.CF_TURB_POWER_LAW_BLASIUS
        self.CF_TURB_POWER_LAW_PRANDTL_LOW_RE = _vsp.CF_TURB_POWER_LAW_PRANDTL_LOW_RE
        self.CF_TURB_POWER_LAW_PRANDTL_MEDIUM_RE = _vsp.CF_TURB_POWER_LAW_PRANDTL_MEDIUM_RE
        self.CF_TURB_POWER_LAW_PRANDTL_HIGH_RE = _vsp.CF_TURB_POWER_LAW_PRANDTL_HIGH_RE
        self.CF_TURB_SCHLICHTING_COMPRESSIBLE = _vsp.CF_TURB_SCHLICHTING_COMPRESSIBLE
        self.DO_NOT_USE_CF_TURB_SCHLICHTING_INCOMPRESSIBLE = _vsp.DO_NOT_USE_CF_TURB_SCHLICHTING_INCOMPRESSIBLE
        self.DO_NOT_USE_CF_TURB_SCHLICHTING_PRANDTL = _vsp.DO_NOT_USE_CF_TURB_SCHLICHTING_PRANDTL
        self.DO_NOT_USE_CF_TURB_SCHULTZ_GRUNOW_HIGH_RE = _vsp.DO_NOT_USE_CF_TURB_SCHULTZ_GRUNOW_HIGH_RE
        self.CF_TURB_SCHULTZ_GRUNOW_SCHOENHERR = _vsp.CF_TURB_SCHULTZ_GRUNOW_SCHOENHERR
        self.DO_NOT_USE_CF_TURB_WHITE_CHRISTOPH_COMPRESSIBLE = _vsp.DO_NOT_USE_CF_TURB_WHITE_CHRISTOPH_COMPRESSIBLE
        self.CF_TURB_ROUGHNESS_SCHLICHTING_AVG = _vsp.CF_TURB_ROUGHNESS_SCHLICHTING_AVG
        self.DO_NOT_USE_CF_TURB_ROUGHNESS_SCHLICHTING_LOCAL = _vsp.DO_NOT_USE_CF_TURB_ROUGHNESS_SCHLICHTING_LOCAL
        self.DO_NOT_USE_CF_TURB_ROUGHNESS_WHITE = _vsp.DO_NOT_USE_CF_TURB_ROUGHNESS_WHITE
        self.CF_TURB_ROUGHNESS_SCHLICHTING_AVG_FLOW_CORRECTION = _vsp.CF_TURB_ROUGHNESS_SCHLICHTING_AVG_FLOW_CORRECTION
        self.CF_TURB_HEATTRANSFER_WHITE_CHRISTOPH = _vsp.CF_TURB_HEATTRANSFER_WHITE_CHRISTOPH
        self.CHEVRON_NONE = _vsp.CHEVRON_NONE
        self.CHEVRON_PARTIAL = _vsp.CHEVRON_PARTIAL
        self.CHEVRON_FULL = _vsp.CHEVRON_FULL
        self.CHEVRON_NUM_TYPES = _vsp.CHEVRON_NUM_TYPES
        self.CHEVRON_W01_SE = _vsp.CHEVRON_W01_SE
        self.CHEVRON_W01_CW = _vsp.CHEVRON_W01_CW
        self.CHEVRON_W01_NUM_MODES = _vsp.CHEVRON_W01_NUM_MODES
        self.COLLISION_OK = _vsp.COLLISION_OK
        self.COLLISION_INTERSECT_NO_SOLUTION = _vsp.COLLISION_INTERSECT_NO_SOLUTION
        self.COLLISION_CLEAR_NO_SOLUTION = _vsp.COLLISION_CLEAR_NO_SOLUTION
        self.NO_FILE_TYPE = _vsp.NO_FILE_TYPE
        self.COMP_GEOM_TXT_TYPE = _vsp.COMP_GEOM_TXT_TYPE
        self.COMP_GEOM_CSV_TYPE = _vsp.COMP_GEOM_CSV_TYPE
        self.DRAG_BUILD_TSV_TYPE_DEPRECATED = _vsp.DRAG_BUILD_TSV_TYPE_DEPRECATED
        self.SLICE_TXT_TYPE = _vsp.SLICE_TXT_TYPE
        self.MASS_PROP_TXT_TYPE = _vsp.MASS_PROP_TXT_TYPE
        self.DEGEN_GEOM_CSV_TYPE = _vsp.DEGEN_GEOM_CSV_TYPE
        self.DEGEN_GEOM_M_TYPE = _vsp.DEGEN_GEOM_M_TYPE
        self.CFD_STL_TYPE = _vsp.CFD_STL_TYPE
        self.CFD_POLY_TYPE = _vsp.CFD_POLY_TYPE
        self.CFD_TRI_TYPE = _vsp.CFD_TRI_TYPE
        self.CFD_OBJ_TYPE = _vsp.CFD_OBJ_TYPE
        self.CFD_DAT_TYPE = _vsp.CFD_DAT_TYPE
        self.CFD_KEY_TYPE = _vsp.CFD_KEY_TYPE
        self.CFD_GMSH_TYPE = _vsp.CFD_GMSH_TYPE
        self.CFD_SRF_TYPE_DEPRECATED = _vsp.CFD_SRF_TYPE_DEPRECATED
        self.CFD_TKEY_TYPE = _vsp.CFD_TKEY_TYPE
        self.PROJ_AREA_CSV_TYPE = _vsp.PROJ_AREA_CSV_TYPE
        self.WAVE_DRAG_TXT_TYPE = _vsp.WAVE_DRAG_TXT_TYPE
        self.VSPAERO_PANEL_TRI_TYPE = _vsp.VSPAERO_PANEL_TRI_TYPE
        self.DRAG_BUILD_CSV_TYPE = _vsp.DRAG_BUILD_CSV_TYPE
        self.CFD_FACET_TYPE = _vsp.CFD_FACET_TYPE
        self.CFD_CURV_TYPE_DEPRECATED = _vsp.CFD_CURV_TYPE_DEPRECATED
        self.CFD_PLOT3D_TYPE_DEPRECATED = _vsp.CFD_PLOT3D_TYPE_DEPRECATED
        self.CFD_VSPGEOM_TYPE = _vsp.CFD_VSPGEOM_TYPE
        self.VSPAERO_VSPGEOM_TYPE = _vsp.VSPAERO_VSPGEOM_TYPE
        self.U_TRIM = _vsp.U_TRIM
        self.L_TRIM = _vsp.L_TRIM
        self.ETA_TRIM = _vsp.ETA_TRIM
        self.NUM_TRIM_TYPES = _vsp.NUM_TRIM_TYPES
        self.DELIM_COMMA = _vsp.DELIM_COMMA
        self.DELIM_USCORE = _vsp.DELIM_USCORE
        self.DELIM_SPACE = _vsp.DELIM_SPACE
        self.DELIM_NONE = _vsp.DELIM_NONE
        self.DELIM_NUM_TYPES = _vsp.DELIM_NUM_TYPES
        self.DEPTH_FRONT = _vsp.DEPTH_FRONT
        self.DEPTH_REAR = _vsp.DEPTH_REAR
        self.DEPTH_FREE = _vsp.DEPTH_FREE
        self.NUM_DEPTH_TYPE = _vsp.NUM_DEPTH_TYPE
        self.SET_3D = _vsp.SET_3D
        self.SET_2D = _vsp.SET_2D
        self.X_DIR = _vsp.X_DIR
        self.Y_DIR = _vsp.Y_DIR
        self.Z_DIR = _vsp.Z_DIR
        self.ALL_DIR = _vsp.ALL_DIR
        self.DISPLAY_BEZIER = _vsp.DISPLAY_BEZIER
        self.DISPLAY_DEGEN_SURF = _vsp.DISPLAY_DEGEN_SURF
        self.DISPLAY_DEGEN_PLATE = _vsp.DISPLAY_DEGEN_PLATE
        self.DISPLAY_DEGEN_CAMBER = _vsp.DISPLAY_DEGEN_CAMBER
        self.GEOM_DRAW_WIRE = _vsp.GEOM_DRAW_WIRE
        self.GEOM_DRAW_HIDDEN = _vsp.GEOM_DRAW_HIDDEN
        self.GEOM_DRAW_SHADE = _vsp.GEOM_DRAW_SHADE
        self.GEOM_DRAW_TEXTURE = _vsp.GEOM_DRAW_TEXTURE
        self.GEOM_DRAW_NONE = _vsp.GEOM_DRAW_NONE
        self.ENGINE_GEOM_NONE = _vsp.ENGINE_GEOM_NONE
        self.ENGINE_GEOM_INLET = _vsp.ENGINE_GEOM_INLET
        self.ENGINE_GEOM_INLET_OUTLET = _vsp.ENGINE_GEOM_INLET_OUTLET
        self.ENGINE_GEOM_OUTLET = _vsp.ENGINE_GEOM_OUTLET
        self.ENGINE_GEOM_IO_NUM_TYPES = _vsp.ENGINE_GEOM_IO_NUM_TYPES
        self.ENGINE_GEOM_FLOWTHROUGH = _vsp.ENGINE_GEOM_FLOWTHROUGH
        self.ENGINE_GEOM_TO_LIP = _vsp.ENGINE_GEOM_TO_LIP
        self.ENGINE_GEOM_FLOWPATH = _vsp.ENGINE_GEOM_FLOWPATH
        self.ENGINE_GEOM_TO_FACE = _vsp.ENGINE_GEOM_TO_FACE
        self.ENGINE_GEOM_NUM_TYPES = _vsp.ENGINE_GEOM_NUM_TYPES
        self.ENGINE_LOC_INDEX = _vsp.ENGINE_LOC_INDEX
        self.ENGINE_LOC_U = _vsp.ENGINE_LOC_U
        self.ENGINE_LOC_INLET_LIP = _vsp.ENGINE_LOC_INLET_LIP
        self.ENGINE_LOC_INLET_FACE = _vsp.ENGINE_LOC_INLET_FACE
        self.ENGINE_LOC_OUTLET_LIP = _vsp.ENGINE_LOC_OUTLET_LIP
        self.ENGINE_LOC_OUTLET_FACE = _vsp.ENGINE_LOC_OUTLET_FACE
        self.ENGINE_LOC_NUM = _vsp.ENGINE_LOC_NUM
        self.ENGINE_MODE_FLOWTHROUGH = _vsp.ENGINE_MODE_FLOWTHROUGH
        self.ENGINE_MODE_FLOWTHROUGH_NEG = _vsp.ENGINE_MODE_FLOWTHROUGH_NEG
        self.ENGINE_MODE_FLOWTHROUGH_NEG_ONLY = _vsp.ENGINE_MODE_FLOWTHROUGH_NEG_ONLY
        self.ENGINE_MODE_TO_LIP = _vsp.ENGINE_MODE_TO_LIP
        self.ENGINE_MODE_TO_FACE = _vsp.ENGINE_MODE_TO_FACE
        self.ENGINE_MODE_TO_FACE_NEG = _vsp.ENGINE_MODE_TO_FACE_NEG
        self.ENGINE_MODE_TO_FACE_NEG_ONLY = _vsp.ENGINE_MODE_TO_FACE_NEG_ONLY
        self.ENGINE_MODE_EXTEND = _vsp.ENGINE_MODE_EXTEND
        self.ENGINE_MODE_NUM_TYPES = _vsp.ENGINE_MODE_NUM_TYPES
        self.VSP_UNKNOWN = _vsp.VSP_UNKNOWN
        self.VSP_OK = _vsp.VSP_OK
        self.VSP_INVALID_PTR = _vsp.VSP_INVALID_PTR
        self.VSP_INVALID_TYPE = _vsp.VSP_INVALID_TYPE
        self.VSP_CANT_FIND_TYPE = _vsp.VSP_CANT_FIND_TYPE
        self.VSP_CANT_FIND_PARM = _vsp.VSP_CANT_FIND_PARM
        self.VSP_CANT_FIND_NAME = _vsp.VSP_CANT_FIND_NAME
        self.VSP_INVALID_GEOM_ID = _vsp.VSP_INVALID_GEOM_ID
        self.VSP_FILE_DOES_NOT_EXIST = _vsp.VSP_FILE_DOES_NOT_EXIST
        self.VSP_FILE_WRITE_FAILURE = _vsp.VSP_FILE_WRITE_FAILURE
        self.VSP_FILE_READ_FAILURE = _vsp.VSP_FILE_READ_FAILURE
        self.VSP_WRONG_GEOM_TYPE = _vsp.VSP_WRONG_GEOM_TYPE
        self.VSP_WRONG_XSEC_TYPE = _vsp.VSP_WRONG_XSEC_TYPE
        self.VSP_WRONG_FILE_TYPE = _vsp.VSP_WRONG_FILE_TYPE
        self.VSP_INDEX_OUT_RANGE = _vsp.VSP_INDEX_OUT_RANGE
        self.VSP_INVALID_XSEC_ID = _vsp.VSP_INVALID_XSEC_ID
        self.VSP_INVALID_ID = _vsp.VSP_INVALID_ID
        self.VSP_CANT_SET_NOT_EQ_PARM = _vsp.VSP_CANT_SET_NOT_EQ_PARM
        self.VSP_AMBIGUOUS_SUBSURF = _vsp.VSP_AMBIGUOUS_SUBSURF
        self.VSP_INVALID_VARPRESET_SETNAME = _vsp.VSP_INVALID_VARPRESET_SETNAME
        self.VSP_INVALID_VARPRESET_GROUPNAME = _vsp.VSP_INVALID_VARPRESET_GROUPNAME
        self.VSP_CONFORMAL_PARENT_UNSUPPORTED = _vsp.VSP_CONFORMAL_PARENT_UNSUPPORTED
        self.VSP_UNEXPECTED_RESET_REMAP_ID = _vsp.VSP_UNEXPECTED_RESET_REMAP_ID
        self.VSP_INVALID_INPUT_VAL = _vsp.VSP_INVALID_INPUT_VAL
        self.VSP_INVALID_CF_EQN = _vsp.VSP_INVALID_CF_EQN
        self.VSP_INVALID_DRIVERS = _vsp.VSP_INVALID_DRIVERS
        self.VSP_ADV_LINK_BUILD_FAIL = _vsp.VSP_ADV_LINK_BUILD_FAIL
        self.VSP_DEPRECATED = _vsp.VSP_DEPRECATED
        self.VSP_LINK_LOOP_DETECTED = _vsp.VSP_LINK_LOOP_DETECTED
        self.VSP_LINK_OUTPUT_NOT_ASSIGNED = _vsp.VSP_LINK_OUTPUT_NOT_ASSIGNED
        self.VSP_DUPLICATE_NAME = _vsp.VSP_DUPLICATE_NAME
        self.VSP_GUI_DEVICE_DEACTIVATED = _vsp.VSP_GUI_DEVICE_DEACTIVATED
        self.VSP_COULD_NOT_CREATE_BACKGROUND3D = _vsp.VSP_COULD_NOT_CREATE_BACKGROUND3D
        self.VSP_NUM_ERROR_CODE = _vsp.VSP_NUM_ERROR_CODE
        self.EXCRESCENCE_COUNT = _vsp.EXCRESCENCE_COUNT
        self.EXCRESCENCE_CD = _vsp.EXCRESCENCE_CD
        self.EXCRESCENCE_PERCENT_GEOM = _vsp.EXCRESCENCE_PERCENT_GEOM
        self.EXCRESCENCE_MARGIN = _vsp.EXCRESCENCE_MARGIN
        self.EXCRESCENCE_DRAGAREA = _vsp.EXCRESCENCE_DRAGAREA
        self.EXPORT_FELISA = _vsp.EXPORT_FELISA
        self.EXPORT_XSEC = _vsp.EXPORT_XSEC
        self.EXPORT_STL = _vsp.EXPORT_STL
        self.EXPORT_AWAVE = _vsp.EXPORT_AWAVE
        self.EXPORT_NASCART = _vsp.EXPORT_NASCART
        self.EXPORT_POVRAY = _vsp.EXPORT_POVRAY
        self.EXPORT_CART3D = _vsp.EXPORT_CART3D
        self.EXPORT_VSPGEOM = _vsp.EXPORT_VSPGEOM
        self.EXPORT_VORXSEC = _vsp.EXPORT_VORXSEC
        self.EXPORT_XSECGEOM = _vsp.EXPORT_XSECGEOM
        self.EXPORT_GMSH = _vsp.EXPORT_GMSH
        self.EXPORT_X3D = _vsp.EXPORT_X3D
        self.EXPORT_STEP = _vsp.EXPORT_STEP
        self.EXPORT_PLOT3D = _vsp.EXPORT_PLOT3D
        self.EXPORT_IGES = _vsp.EXPORT_IGES
        self.EXPORT_BEM = _vsp.EXPORT_BEM
        self.EXPORT_DXF = _vsp.EXPORT_DXF
        self.EXPORT_FACET = _vsp.EXPORT_FACET
        self.EXPORT_SVG = _vsp.EXPORT_SVG
        self.EXPORT_PMARC = _vsp.EXPORT_PMARC
        self.EXPORT_OBJ = _vsp.EXPORT_OBJ
        self.EXPORT_SELIG_AIRFOIL = _vsp.EXPORT_SELIG_AIRFOIL
        self.EXPORT_BEZIER_AIRFOIL = _vsp.EXPORT_BEZIER_AIRFOIL
        self.EXPORT_IGES_STRUCTURE = _vsp.EXPORT_IGES_STRUCTURE
        self.EXPORT_STEP_STRUCTURE = _vsp.EXPORT_STEP_STRUCTURE
        self.FEA_BCM_USER = _vsp.FEA_BCM_USER
        self.FEA_BCM_ALL = _vsp.FEA_BCM_ALL
        self.FEA_BCM_PIN = _vsp.FEA_BCM_PIN
        self.FEA_BCM_SYMM = _vsp.FEA_BCM_SYMM
        self.FEA_BCM_ASYMM = _vsp.FEA_BCM_ASYMM
        self.FEA_NUM_BCM_MODES = _vsp.FEA_NUM_BCM_MODES
        self.FEA_BC_STRUCTURE = _vsp.FEA_BC_STRUCTURE
        self.FEA_BC_PART = _vsp.FEA_BC_PART
        self.FEA_BC_SUBSURF = _vsp.FEA_BC_SUBSURF
        self.FEA_NUM_BC_TYPES = _vsp.FEA_NUM_BC_TYPES
        self.FEA_XSEC_GENERAL = _vsp.FEA_XSEC_GENERAL
        self.FEA_XSEC_CIRC = _vsp.FEA_XSEC_CIRC
        self.FEA_XSEC_PIPE = _vsp.FEA_XSEC_PIPE
        self.FEA_XSEC_I = _vsp.FEA_XSEC_I
        self.FEA_XSEC_RECT = _vsp.FEA_XSEC_RECT
        self.FEA_XSEC_BOX = _vsp.FEA_XSEC_BOX
        self.FEA_MASS_FILE_NAME = _vsp.FEA_MASS_FILE_NAME
        self.FEA_NASTRAN_FILE_NAME = _vsp.FEA_NASTRAN_FILE_NAME
        self.FEA_NKEY_FILE_NAME = _vsp.FEA_NKEY_FILE_NAME
        self.FEA_CALCULIX_FILE_NAME = _vsp.FEA_CALCULIX_FILE_NAME
        self.FEA_STL_FILE_NAME = _vsp.FEA_STL_FILE_NAME
        self.FEA_GMSH_FILE_NAME = _vsp.FEA_GMSH_FILE_NAME
        self.FEA_SRF_FILE_NAME = _vsp.FEA_SRF_FILE_NAME
        self.FEA_CURV_FILE_NAME = _vsp.FEA_CURV_FILE_NAME
        self.FEA_PLOT3D_FILE_NAME = _vsp.FEA_PLOT3D_FILE_NAME
        self.FEA_IGES_FILE_NAME = _vsp.FEA_IGES_FILE_NAME
        self.FEA_STEP_FILE_NAME = _vsp.FEA_STEP_FILE_NAME
        self.FEA_NUM_FILE_NAMES = _vsp.FEA_NUM_FILE_NAMES
        self.FEA_FIX_PT_ON_BODY = _vsp.FEA_FIX_PT_ON_BODY
        self.FEA_FIX_PT_GLOBAL_XYZ = _vsp.FEA_FIX_PT_GLOBAL_XYZ
        self.FEA_FIX_PT_DELTA_XYZ = _vsp.FEA_FIX_PT_DELTA_XYZ
        self.FEA_FIX_PT_DELTA_UVN = _vsp.FEA_FIX_PT_DELTA_UVN
        self.FEA_FIX_PT_GEOM_ORIGIN = _vsp.FEA_FIX_PT_GEOM_ORIGIN
        self.FEA_FIX_PT_GEOM_CG = _vsp.FEA_FIX_PT_GEOM_CG
        self.FEA_NUM_FIX_PT_TYPES = _vsp.FEA_NUM_FIX_PT_TYPES
        self.FEA_ISOTROPIC = _vsp.FEA_ISOTROPIC
        self.FEA_ENG_ORTHO = _vsp.FEA_ENG_ORTHO
        self.FEA_ENG_ORTHO_TRANS_ISO = _vsp.FEA_ENG_ORTHO_TRANS_ISO
        self.FEA_LAMINATE = _vsp.FEA_LAMINATE
        self.FEA_NUM_MAT_TYPES = _vsp.FEA_NUM_MAT_TYPES
        self.FEA_ORIENT_GLOBAL_X = _vsp.FEA_ORIENT_GLOBAL_X
        self.FEA_ORIENT_GLOBAL_Y = _vsp.FEA_ORIENT_GLOBAL_Y
        self.FEA_ORIENT_GLOBAL_Z = _vsp.FEA_ORIENT_GLOBAL_Z
        self.FEA_ORIENT_COMP_X = _vsp.FEA_ORIENT_COMP_X
        self.FEA_ORIENT_COMP_Y = _vsp.FEA_ORIENT_COMP_Y
        self.FEA_ORIENT_COMP_Z = _vsp.FEA_ORIENT_COMP_Z
        self.FEA_ORIENT_PART_U = _vsp.FEA_ORIENT_PART_U
        self.FEA_ORIENT_PART_V = _vsp.FEA_ORIENT_PART_V
        self.FEA_ORIENT_OML_U = _vsp.FEA_ORIENT_OML_U
        self.FEA_ORIENT_OML_V = _vsp.FEA_ORIENT_OML_V
        self.FEA_ORIENT_OML_R = _vsp.FEA_ORIENT_OML_R
        self.FEA_ORIENT_OML_S = _vsp.FEA_ORIENT_OML_S
        self.FEA_ORIENT_OML_T = _vsp.FEA_ORIENT_OML_T
        self.FEA_NUM_ORIENT_TYPES = _vsp.FEA_NUM_ORIENT_TYPES
        self.FEA_DEPRECATED = _vsp.FEA_DEPRECATED
        self.FEA_SHELL = _vsp.FEA_SHELL
        self.FEA_BEAM = _vsp.FEA_BEAM
        self.FEA_SHELL_AND_BEAM = _vsp.FEA_SHELL_AND_BEAM
        self.FEA_NO_ELEMENTS = _vsp.FEA_NO_ELEMENTS
        self.FEA_NUM_ELEMENT_TYPES = _vsp.FEA_NUM_ELEMENT_TYPES
        self.FEA_SLICE = _vsp.FEA_SLICE
        self.FEA_RIB = _vsp.FEA_RIB
        self.FEA_SPAR = _vsp.FEA_SPAR
        self.FEA_FIX_POINT = _vsp.FEA_FIX_POINT
        self.FEA_DOME = _vsp.FEA_DOME
        self.FEA_RIB_ARRAY = _vsp.FEA_RIB_ARRAY
        self.FEA_SLICE_ARRAY = _vsp.FEA_SLICE_ARRAY
        self.FEA_SKIN = _vsp.FEA_SKIN
        self.FEA_TRIM = _vsp.FEA_TRIM
        self.FEA_POLY_SPAR = _vsp.FEA_POLY_SPAR
        self.FEA_NUM_TYPES = _vsp.FEA_NUM_TYPES
        self.NO_NORMAL = _vsp.NO_NORMAL
        self.LE_NORMAL = _vsp.LE_NORMAL
        self.TE_NORMAL = _vsp.TE_NORMAL
        self.SPAR_NORMAL = _vsp.SPAR_NORMAL
        self.FEA_KEEP = _vsp.FEA_KEEP
        self.FEA_DELETE = _vsp.FEA_DELETE
        self.FEA_NUM_SHELL_TREATMENT_TYPES = _vsp.FEA_NUM_SHELL_TREATMENT_TYPES
        self.XY_BODY = _vsp.XY_BODY
        self.YZ_BODY = _vsp.YZ_BODY
        self.XZ_BODY = _vsp.XZ_BODY
        self.XY_ABS = _vsp.XY_ABS
        self.YZ_ABS = _vsp.YZ_ABS
        self.XZ_ABS = _vsp.XZ_ABS
        self.SPINE_NORMAL = _vsp.SPINE_NORMAL
        self.POLY_SPAR_POINT_U01 = _vsp.POLY_SPAR_POINT_U01
        self.POLY_SPAR_POINT_U0N = _vsp.POLY_SPAR_POINT_U0N
        self.POLY_SPAR_POINT_ETA = _vsp.POLY_SPAR_POINT_ETA
        self.NUM_POLY_SPAR_POINT_TYPES = _vsp.NUM_POLY_SPAR_POINT_TYPES
        self.SI_UNIT = _vsp.SI_UNIT
        self.CGS_UNIT = _vsp.CGS_UNIT
        self.MPA_UNIT = _vsp.MPA_UNIT
        self.BFT_UNIT = _vsp.BFT_UNIT
        self.BIN_UNIT = _vsp.BIN_UNIT
        self.FF_B_MANUAL = _vsp.FF_B_MANUAL
        self.FF_B_SCHEMENSKY_FUSE = _vsp.FF_B_SCHEMENSKY_FUSE
        self.FF_B_SCHEMENSKY_NACELLE = _vsp.FF_B_SCHEMENSKY_NACELLE
        self.FF_B_HOERNER_STREAMBODY = _vsp.FF_B_HOERNER_STREAMBODY
        self.FF_B_TORENBEEK = _vsp.FF_B_TORENBEEK
        self.FF_B_SHEVELL = _vsp.FF_B_SHEVELL
        self.FF_B_COVERT = _vsp.FF_B_COVERT
        self.FF_B_JENKINSON_FUSE = _vsp.FF_B_JENKINSON_FUSE
        self.FF_B_JENKINSON_WING_NACELLE = _vsp.FF_B_JENKINSON_WING_NACELLE
        self.FF_B_JENKINSON_AFT_FUSE_NACELLE = _vsp.FF_B_JENKINSON_AFT_FUSE_NACELLE
        self.FF_W_MANUAL = _vsp.FF_W_MANUAL
        self.FF_W_EDET_CONV = _vsp.FF_W_EDET_CONV
        self.FF_W_EDET_ADV = _vsp.FF_W_EDET_ADV
        self.FF_W_HOERNER = _vsp.FF_W_HOERNER
        self.FF_W_COVERT = _vsp.FF_W_COVERT
        self.FF_W_SHEVELL = _vsp.FF_W_SHEVELL
        self.FF_W_KROO = _vsp.FF_W_KROO
        self.FF_W_TORENBEEK = _vsp.FF_W_TORENBEEK
        self.FF_W_DATCOM = _vsp.FF_W_DATCOM
        self.FF_W_SCHEMENSKY_6_SERIES_AF = _vsp.FF_W_SCHEMENSKY_6_SERIES_AF
        self.FF_W_SCHEMENSKY_4_SERIES_AF = _vsp.FF_W_SCHEMENSKY_4_SERIES_AF
        self.FF_W_JENKINSON_WING = _vsp.FF_W_JENKINSON_WING
        self.FF_W_JENKINSON_TAIL = _vsp.FF_W_JENKINSON_TAIL
        self.FF_W_SCHEMENSKY_SUPERCRITICAL_AF = _vsp.FF_W_SCHEMENSKY_SUPERCRITICAL_AF
        self.ONE_THIRD_ROTOR_FRAGMENT = _vsp.ONE_THIRD_ROTOR_FRAGMENT
        self.INTERMEDIATE_FRAGMENT = _vsp.INTERMEDIATE_FRAGMENT
        self.ALTERNATE_FRAGMENT = _vsp.ALTERNATE_FRAGMENT
        self.FAN_FRAGMENT = _vsp.FAN_FRAGMENT
        self.SMALL_FRAGMENT = _vsp.SMALL_FRAGMENT
        self.GENERIC_FRAGMENT = _vsp.GENERIC_FRAGMENT
        self.NUM_FRAGMENT_MODES = _vsp.NUM_FRAGMENT_MODES
        self.PD_UNITS_IMPERIAL = _vsp.PD_UNITS_IMPERIAL
        self.PD_UNITS_METRIC = _vsp.PD_UNITS_METRIC
        self.OPEN = _vsp.OPEN
        self.SAVE = _vsp.SAVE
        self.NUM_FILE_CHOOSER_MODES = _vsp.NUM_FILE_CHOOSER_MODES
        self.FC_OPENVSP = _vsp.FC_OPENVSP
        self.FC_NATIVE = _vsp.FC_NATIVE
        self.NUM_FILE_CHOOSER_TYPES = _vsp.NUM_FILE_CHOOSER_TYPES
        self.GDEV_TAB = _vsp.GDEV_TAB
        self.GDEV_SCROLL_TAB = _vsp.GDEV_SCROLL_TAB
        self.GDEV_GROUP = _vsp.GDEV_GROUP
        self.GDEV_PARM_BUTTON = _vsp.GDEV_PARM_BUTTON
        self.GDEV_INPUT = _vsp.GDEV_INPUT
        self.GDEV_OUTPUT = _vsp.GDEV_OUTPUT
        self.GDEV_SLIDER = _vsp.GDEV_SLIDER
        self.GDEV_SLIDER_ADJ_RANGE = _vsp.GDEV_SLIDER_ADJ_RANGE
        self.GDEV_CHECK_BUTTON = _vsp.GDEV_CHECK_BUTTON
        self.GDEV_CHECK_BUTTON_BIT = _vsp.GDEV_CHECK_BUTTON_BIT
        self.GDEV_RADIO_BUTTON = _vsp.GDEV_RADIO_BUTTON
        self.GDEV_TOGGLE_BUTTON = _vsp.GDEV_TOGGLE_BUTTON
        self.GDEV_TOGGLE_BUTTON_FREE = _vsp.GDEV_TOGGLE_BUTTON_FREE
        self.GDEV_TOGGLE_RADIO_GROUP = _vsp.GDEV_TOGGLE_RADIO_GROUP
        self.GDEV_TRIGGER_BUTTON = _vsp.GDEV_TRIGGER_BUTTON
        self.GDEV_COUNTER = _vsp.GDEV_COUNTER
        self.GDEV_CHOICE = _vsp.GDEV_CHOICE
        self.GDEV_ADD_CHOICE_ITEM = _vsp.GDEV_ADD_CHOICE_ITEM
        self.GDEV_SLIDER_INPUT = _vsp.GDEV_SLIDER_INPUT
        self.GDEV_SLIDER_ADJ_RANGE_INPUT = _vsp.GDEV_SLIDER_ADJ_RANGE_INPUT
        self.GDEV_SLIDER_ADJ_RANGE_TWO_INPUT = _vsp.GDEV_SLIDER_ADJ_RANGE_TWO_INPUT
        self.GDEV_FRACT_PARM_SLIDER = _vsp.GDEV_FRACT_PARM_SLIDER
        self.GDEV_STRING_INPUT = _vsp.GDEV_STRING_INPUT
        self.GDEV_INDEX_SELECTOR = _vsp.GDEV_INDEX_SELECTOR
        self.GDEV_COLOR_PICKER = _vsp.GDEV_COLOR_PICKER
        self.GDEV_YGAP = _vsp.GDEV_YGAP
        self.GDEV_DIVIDER_BOX = _vsp.GDEV_DIVIDER_BOX
        self.GDEV_BEGIN_SAME_LINE = _vsp.GDEV_BEGIN_SAME_LINE
        self.GDEV_END_SAME_LINE = _vsp.GDEV_END_SAME_LINE
        self.GDEV_FORCE_WIDTH = _vsp.GDEV_FORCE_WIDTH
        self.GDEV_SET_FORMAT = _vsp.GDEV_SET_FORMAT
        self.NUM_GDEV_TYPES = _vsp.NUM_GDEV_TYPES
        self.ALL_GDEV_TYPES = _vsp.ALL_GDEV_TYPES
        self.GEAR_CONFIGURATION_DOWN = _vsp.GEAR_CONFIGURATION_DOWN
        self.GEAR_CONFIGURATION_UP = _vsp.GEAR_CONFIGURATION_UP
        self.GEAR_CONFIGURATION_UP_AND_DOWN = _vsp.GEAR_CONFIGURATION_UP_AND_DOWN
        self.GEAR_CONFIGURATION_INTERMEDIATE = _vsp.GEAR_CONFIGURATION_INTERMEDIATE
        self.GEAR_CONFIGURATION_ALL = _vsp.GEAR_CONFIGURATION_ALL
        self.NUM_GEAR_CONFIGURATION_MODES = _vsp.NUM_GEAR_CONFIGURATION_MODES
        self.GEAR_STOWED_POSITION = _vsp.GEAR_STOWED_POSITION
        self.GEAR_MECHANISM = _vsp.GEAR_MECHANISM
        self.NUM_GEAR_RETRACT_MODES = _vsp.NUM_GEAR_RETRACT_MODES
        self.GEAR_SUSPENSION_NOMINAL = _vsp.GEAR_SUSPENSION_NOMINAL
        self.GEAR_SUSPENSION_COMPRESSED = _vsp.GEAR_SUSPENSION_COMPRESSED
        self.GEAR_SUSPENSION_EXTENDED = _vsp.GEAR_SUSPENSION_EXTENDED
        self.NUM_GEAR_SUSPENSION_MODES = _vsp.NUM_GEAR_SUSPENSION_MODES
        self.MALE = _vsp.MALE
        self.FEMALE = _vsp.FEMALE
        self.EXTERNAL_INTERFERENCE = _vsp.EXTERNAL_INTERFERENCE
        self.PACKAGING_INTERFERENCE = _vsp.PACKAGING_INTERFERENCE
        self.EXTERNAL_SELF_INTERFERENCE = _vsp.EXTERNAL_SELF_INTERFERENCE
        self.PLANE_STATIC_DISTANCE_INTERFERENCE = _vsp.PLANE_STATIC_DISTANCE_INTERFERENCE
        self.PLANE_2PT_ANGLE_INTERFERENCE = _vsp.PLANE_2PT_ANGLE_INTERFERENCE
        self.GEAR_CG_TIPBACK_ANALYSIS = _vsp.GEAR_CG_TIPBACK_ANALYSIS
        self.PLANE_1PT_ANGLE_INTERFERENCE = _vsp.PLANE_1PT_ANGLE_INTERFERENCE
        self.GEAR_WEIGHT_DISTRIBUTION_ANALYSIS = _vsp.GEAR_WEIGHT_DISTRIBUTION_ANALYSIS
        self.GEAR_TIPOVER_ANALYSIS = _vsp.GEAR_TIPOVER_ANALYSIS
        self.GEAR_TURN_ANALYSIS = _vsp.GEAR_TURN_ANALYSIS
        self.VISIBLE_FROM_POINT_ANALYSIS = _vsp.VISIBLE_FROM_POINT_ANALYSIS
        self.CCE_INTERFERENCE = _vsp.CCE_INTERFERENCE
        self.LINEAR_SWEPT_VOLUME_ANALYSIS = _vsp.LINEAR_SWEPT_VOLUME_ANALYSIS
        self.VISIBLE_AT_SURF_ANALYSIS = _vsp.VISIBLE_AT_SURF_ANALYSIS
        self.RISK_ANGLE = _vsp.RISK_ANGLE
        self.COMP_GEOM = _vsp.COMP_GEOM
        self.PLANAR_SLICE = _vsp.PLANAR_SLICE
        self.PROJ_AREA = _vsp.PROJ_AREA
        self.MASS_PROP = _vsp.MASS_PROP
        self.AERO_CENTER = _vsp.AERO_CENTER
        self.NUM_INTERFERENCE_TYPES = _vsp.NUM_INTERFERENCE_TYPES
        self.POD_GEOM_SCREEN = _vsp.POD_GEOM_SCREEN
        self.FUSELAGE_GEOM_SCREEN = _vsp.FUSELAGE_GEOM_SCREEN
        self.MS_WING_GEOM_SCREEN = _vsp.MS_WING_GEOM_SCREEN
        self.BLANK_GEOM_SCREEN = _vsp.BLANK_GEOM_SCREEN
        self.MESH_GEOM_SCREEN = _vsp.MESH_GEOM_SCREEN
        self.NGON_MESH_GEOM_SCREEN = _vsp.NGON_MESH_GEOM_SCREEN
        self.STACK_GEOM_SCREEN = _vsp.STACK_GEOM_SCREEN
        self.CUSTOM_GEOM_SCREEN = _vsp.CUSTOM_GEOM_SCREEN
        self.PT_CLOUD_GEOM_SCREEN = _vsp.PT_CLOUD_GEOM_SCREEN
        self.PROP_GEOM_SCREEN = _vsp.PROP_GEOM_SCREEN
        self.HINGE_GEOM_SCREEN = _vsp.HINGE_GEOM_SCREEN
        self.MULT_GEOM_SCREEN = _vsp.MULT_GEOM_SCREEN
        self.CONFORMAL_SCREEN = _vsp.CONFORMAL_SCREEN
        self.ELLIPSOID_GEOM_SCREEN = _vsp.ELLIPSOID_GEOM_SCREEN
        self.BOR_GEOM_SCREEN = _vsp.BOR_GEOM_SCREEN
        self.WIRE_FRAME_GEOM_SCREEN = _vsp.WIRE_FRAME_GEOM_SCREEN
        self.HUMAN_GEOM_SCREEN = _vsp.HUMAN_GEOM_SCREEN
        self.ROUTING_GEOM_SCREEN = _vsp.ROUTING_GEOM_SCREEN
        self.AUXILIARY_GEOM_SCREEN = _vsp.AUXILIARY_GEOM_SCREEN
        self.GEAR_GEOM_SCREEN = _vsp.GEAR_GEOM_SCREEN
        self.COBRA_GEOM_SCREEN = _vsp.COBRA_GEOM_SCREEN
        self.NUM_GEOM_SCREENS = _vsp.NUM_GEOM_SCREENS
        self.ALL_GEOM_SCREENS = _vsp.ALL_GEOM_SCREENS
        self.VSP_ADV_LINK_SCREEN = _vsp.VSP_ADV_LINK_SCREEN
        self.VSP_ADV_LINK_VAR_RENAME_SCREEN = _vsp.VSP_ADV_LINK_VAR_RENAME_SCREEN
        self.VSP_AERO_STRUCT_SCREEN = _vsp.VSP_AERO_STRUCT_SCREEN
        self.VSP_AIRFOIL_CURVES_EXPORT_SCREEN = _vsp.VSP_AIRFOIL_CURVES_EXPORT_SCREEN
        self.VSP_AIRFOIL_POINTS_EXPORT_SCREEN = _vsp.VSP_AIRFOIL_POINTS_EXPORT_SCREEN
        self.VSP_ATTRIBUTE_EXPLORER_SCREEN = _vsp.VSP_ATTRIBUTE_EXPLORER_SCREEN
        self.VSP_BACKGROUND_SCREEN = _vsp.VSP_BACKGROUND_SCREEN
        self.VSP_BACKGROUND3D_SCREEN = _vsp.VSP_BACKGROUND3D_SCREEN
        self.VSP_BACKGROUND3D_PREVIEW_SCREEN = _vsp.VSP_BACKGROUND3D_PREVIEW_SCREEN
        self.VSP_BEM_OPTIONS_SCREEN = _vsp.VSP_BEM_OPTIONS_SCREEN
        self.VSP_CFD_MESH_SCREEN = _vsp.VSP_CFD_MESH_SCREEN
        self.VSP_CLIPPING_SCREEN = _vsp.VSP_CLIPPING_SCREEN
        self.VSP_COMP_GEOM_SCREEN = _vsp.VSP_COMP_GEOM_SCREEN
        self.VSP_COR_SCREEN = _vsp.VSP_COR_SCREEN
        self.VSP_CURVE_EDIT_SCREEN = _vsp.VSP_CURVE_EDIT_SCREEN
        self.VSP_DEGEN_GEOM_SCREEN = _vsp.VSP_DEGEN_GEOM_SCREEN
        self.VSP_DESIGN_VAR_SCREEN = _vsp.VSP_DESIGN_VAR_SCREEN
        self.VSP_DXF_OPTIONS_SCREEN = _vsp.VSP_DXF_OPTIONS_SCREEN
        self.VSP_EXPORT_SCREEN = _vsp.VSP_EXPORT_SCREEN
        self.VSP_FEA_PART_EDIT_SCREEN = _vsp.VSP_FEA_PART_EDIT_SCREEN
        self.VSP_FEA_XSEC_SCREEN = _vsp.VSP_FEA_XSEC_SCREEN
        self.VSP_FIT_MODEL_SCREEN = _vsp.VSP_FIT_MODEL_SCREEN
        self.VSP_IGES_OPTIONS_SCREEN = _vsp.VSP_IGES_OPTIONS_SCREEN
        self.VSP_IGES_STRUCTURE_OPTIONS_SCREEN = _vsp.VSP_IGES_STRUCTURE_OPTIONS_SCREEN
        self.VSP_EXPORT_CUSTOM_SCRIPT = _vsp.VSP_EXPORT_CUSTOM_SCRIPT
        self.VSP_GEOMETRY_ANALYSIS_SCREEN = _vsp.VSP_GEOMETRY_ANALYSIS_SCREEN
        self.VSP_IMPORT_SCREEN = _vsp.VSP_IMPORT_SCREEN
        self.VSP_LIGHTING_SCREEN = _vsp.VSP_LIGHTING_SCREEN
        self.VSP_MANAGE_GEOM_SCREEN = _vsp.VSP_MANAGE_GEOM_SCREEN
        self.VSP_MANAGE_TEXTURE_SCREEN = _vsp.VSP_MANAGE_TEXTURE_SCREEN
        self.VSP_MASS_PROP_SCREEN = _vsp.VSP_MASS_PROP_SCREEN
        self.VSP_MATERIAL_EDIT_SCREEN = _vsp.VSP_MATERIAL_EDIT_SCREEN
        self.VSP_MEASURE_SCREEN = _vsp.VSP_MEASURE_SCREEN
        self.VSP_MODE_EDITOR_SCREEN = _vsp.VSP_MODE_EDITOR_SCREEN
        self.VSP_NERF_MANAGE_GEOM_SCREEN = _vsp.VSP_NERF_MANAGE_GEOM_SCREEN
        self.VSP_SNAP_TO_SCREEN = _vsp.VSP_SNAP_TO_SCREEN
        self.VSP_PARASITE_DRAG_SCREEN = _vsp.VSP_PARASITE_DRAG_SCREEN
        self.VSP_PARM_DEBUG_SCREEN = _vsp.VSP_PARM_DEBUG_SCREEN
        self.VSP_PARM_LINK_SCREEN = _vsp.VSP_PARM_LINK_SCREEN
        self.VSP_PARM_SCREEN = _vsp.VSP_PARM_SCREEN
        self.VSP_PICK_SET_SCREEN = _vsp.VSP_PICK_SET_SCREEN
        self.VSP_PREFERENCES_SCREEN = _vsp.VSP_PREFERENCES_SCREEN
        self.VSP_PROJECTION_SCREEN = _vsp.VSP_PROJECTION_SCREEN
        self.VSP_PSLICE_SCREEN = _vsp.VSP_PSLICE_SCREEN
        self.VSP_RESULTS_VIEWER_SCREEN = _vsp.VSP_RESULTS_VIEWER_SCREEN
        self.VSP_SCREENSHOT_SCREEN = _vsp.VSP_SCREENSHOT_SCREEN
        self.VSP_SELECT_FILE_SCREEN = _vsp.VSP_SELECT_FILE_SCREEN
        self.VSP_SET_EDITOR_SCREEN = _vsp.VSP_SET_EDITOR_SCREEN
        self.VSP_STEP_OPTIONS_SCREEN = _vsp.VSP_STEP_OPTIONS_SCREEN
        self.VSP_STEP_STRUCTURE_OPTIONS_SCREEN = _vsp.VSP_STEP_STRUCTURE_OPTIONS_SCREEN
        self.VSP_STL_OPTIONS_SCREEN = _vsp.VSP_STL_OPTIONS_SCREEN
        self.VSP_STRUCT_SCREEN = _vsp.VSP_STRUCT_SCREEN
        self.VSP_STRUCT_ASSEMBLY_SCREEN = _vsp.VSP_STRUCT_ASSEMBLY_SCREEN
        self.VSP_SURFACE_INTERSECTION_SCREEN = _vsp.VSP_SURFACE_INTERSECTION_SCREEN
        self.VSP_SVG_OPTIONS_SCREEN = _vsp.VSP_SVG_OPTIONS_SCREEN
        self.VSP_USER_PARM_SCREEN = _vsp.VSP_USER_PARM_SCREEN
        self.VSP_VAR_PRESET_SCREEN = _vsp.VSP_VAR_PRESET_SCREEN
        self.VSP_VEH_NOTES_SCREEN = _vsp.VSP_VEH_NOTES_SCREEN
        self.VSP_VEH_SCREEN = _vsp.VSP_VEH_SCREEN
        self.VSP_VIEW_SCREEN = _vsp.VSP_VIEW_SCREEN
        self.VSP_VSPAERO_PLOT_SCREEN = _vsp.VSP_VSPAERO_PLOT_SCREEN
        self.VSP_VSPAERO_SCREEN = _vsp.VSP_VSPAERO_SCREEN
        self.VSP_XSEC_SCREEN = _vsp.VSP_XSEC_SCREEN
        self.VSP_WAVEDRAG_SCREEN = _vsp.VSP_WAVEDRAG_SCREEN
        self.VSP_MAIN_SCREEN = _vsp.VSP_MAIN_SCREEN
        self.VSP_NUM_SCREENS = _vsp.VSP_NUM_SCREENS
        self.VSP_ALL_SCREENS = _vsp.VSP_ALL_SCREENS
        self.EDIT_XSEC_CIRCLE = _vsp.EDIT_XSEC_CIRCLE
        self.EDIT_XSEC_ELLIPSE = _vsp.EDIT_XSEC_ELLIPSE
        self.EDIT_XSEC_RECTANGLE = _vsp.EDIT_XSEC_RECTANGLE
        self.NUM_INIT_EDIT_XSEC_TYPES = _vsp.NUM_INIT_EDIT_XSEC_TYPES
        self.IMPORT_STL = _vsp.IMPORT_STL
        self.IMPORT_NASCART = _vsp.IMPORT_NASCART
        self.IMPORT_CART3D_TRI = _vsp.IMPORT_CART3D_TRI
        self.IMPORT_XSEC_MESH = _vsp.IMPORT_XSEC_MESH
        self.IMPORT_PTS = _vsp.IMPORT_PTS
        self.IMPORT_V2 = _vsp.IMPORT_V2
        self.IMPORT_BEM = _vsp.IMPORT_BEM
        self.IMPORT_XSEC_WIRE = _vsp.IMPORT_XSEC_WIRE
        self.IMPORT_P3D_WIRE = _vsp.IMPORT_P3D_WIRE
        self.INTERSECT_SRF_FILE_NAME = _vsp.INTERSECT_SRF_FILE_NAME
        self.INTERSECT_CURV_FILE_NAME = _vsp.INTERSECT_CURV_FILE_NAME
        self.INTERSECT_PLOT3D_FILE_NAME = _vsp.INTERSECT_PLOT3D_FILE_NAME
        self.INTERSECT_IGES_FILE_NAME = _vsp.INTERSECT_IGES_FILE_NAME
        self.INTERSECT_STEP_FILE_NAME = _vsp.INTERSECT_STEP_FILE_NAME
        self.INTERSECT_NUM_FILE_NAMES = _vsp.INTERSECT_NUM_FILE_NAMES
        self.LEN_MM = _vsp.LEN_MM
        self.LEN_CM = _vsp.LEN_CM
        self.LEN_M = _vsp.LEN_M
        self.LEN_IN = _vsp.LEN_IN
        self.LEN_FT = _vsp.LEN_FT
        self.LEN_YD = _vsp.LEN_YD
        self.LEN_UNITLESS = _vsp.LEN_UNITLESS
        self.NUM_LEN_UNIT = _vsp.NUM_LEN_UNIT
        self.MASS_UNIT_G = _vsp.MASS_UNIT_G
        self.MASS_UNIT_KG = _vsp.MASS_UNIT_KG
        self.MASS_UNIT_TONNE = _vsp.MASS_UNIT_TONNE
        self.MASS_UNIT_LBM = _vsp.MASS_UNIT_LBM
        self.MASS_UNIT_SLUG = _vsp.MASS_UNIT_SLUG
        self.MASS_LBFSEC2IN = _vsp.MASS_LBFSEC2IN
        self.NUM_MASS_UNIT = _vsp.NUM_MASS_UNIT
        self.NO_REASON = _vsp.NO_REASON
        self.MAX_LEN_CONSTRAINT = _vsp.MAX_LEN_CONSTRAINT
        self.CURV_GAP = _vsp.CURV_GAP
        self.CURV_NCIRCSEG = _vsp.CURV_NCIRCSEG
        self.SOURCES = _vsp.SOURCES
        self.MIN_LEN_CONSTRAINT = _vsp.MIN_LEN_CONSTRAINT
        self.MIN_LEN_CONSTRAINT_CURV_GAP = _vsp.MIN_LEN_CONSTRAINT_CURV_GAP
        self.MIN_LEN_CONSTRAINT_CURV_NCIRCSEG = _vsp.MIN_LEN_CONSTRAINT_CURV_NCIRCSEG
        self.MIN_LEN_CONSTRAINT_SOURCES = _vsp.MIN_LEN_CONSTRAINT_SOURCES
        self.GROW_LIMIT_MAX_LEN_CONSTRAINT = _vsp.GROW_LIMIT_MAX_LEN_CONSTRAINT
        self.GROW_LIMIT_CURV_GAP = _vsp.GROW_LIMIT_CURV_GAP
        self.GROW_LIMIT_CURV_NCIRCSEG = _vsp.GROW_LIMIT_CURV_NCIRCSEG
        self.GROW_LIMIT_SOURCES = _vsp.GROW_LIMIT_SOURCES
        self.GROW_LIMIT_MIN_LEN_CONSTRAINT = _vsp.GROW_LIMIT_MIN_LEN_CONSTRAINT
        self.GROW_LIMIT_MIN_LEN_CONSTRAINT_CURV_GAP = _vsp.GROW_LIMIT_MIN_LEN_CONSTRAINT_CURV_GAP
        self.GROW_LIMIT_MIN_LEN_CONSTRAINT_CURV_NCIRCSEG = _vsp.GROW_LIMIT_MIN_LEN_CONSTRAINT_CURV_NCIRCSEG
        self.GROW_LIMIT_MIN_LEN_CONSTRAINT_SOURCES = _vsp.GROW_LIMIT_MIN_LEN_CONSTRAINT_SOURCES
        self.NUM_MESH_REASON = _vsp.NUM_MESH_REASON
        self.MIN_LEN_INCREMENT = _vsp.MIN_LEN_INCREMENT
        self.GROW_LIMIT_INCREMENT = _vsp.GROW_LIMIT_INCREMENT
        self.MIN_GROW_LIMIT = _vsp.MIN_GROW_LIMIT
        self.TRI_MESH_TYPE = _vsp.TRI_MESH_TYPE
        self.QUAD_MESH_TYPE = _vsp.QUAD_MESH_TYPE
        self.NGON_MESH_TYPE = _vsp.NGON_MESH_TYPE
        self.NUM_MESH_TYPE = _vsp.NUM_MESH_TYPE
        self.EXTENT_FORWARD_INF = _vsp.EXTENT_FORWARD_INF
        self.EXTENT_REVERSE_INF = _vsp.EXTENT_REVERSE_INF
        self.EXTENT_FORWARD_FINITE = _vsp.EXTENT_FORWARD_FINITE
        self.EXTENT_REVERSE_FINITE = _vsp.EXTENT_REVERSE_FINITE
        self.EXTENT_SLIDER_FULL = _vsp.EXTENT_SLIDER_FULL
        self.EXTENT_SLIDER_BEFORE = _vsp.EXTENT_SLIDER_BEFORE
        self.EXTENT_SLIDER_AFTER = _vsp.EXTENT_SLIDER_AFTER
        self.MOTION_NUM_EXTENT_TYPES = _vsp.MOTION_NUM_EXTENT_TYPES
        self.ID_LENGTH_PRESET_GROUP = _vsp.ID_LENGTH_PRESET_GROUP
        self.ID_LENGTH_PRESET_SETTING = _vsp.ID_LENGTH_PRESET_SETTING
        self.ID_LENGTH_ATTR = _vsp.ID_LENGTH_ATTR
        self.ID_LENGTH_ATTRCOLL = _vsp.ID_LENGTH_ATTRCOLL
        self.ID_LENGTH_PARMCONTAINER = _vsp.ID_LENGTH_PARMCONTAINER
        self.ID_LENGTH_PARM = _vsp.ID_LENGTH_PARM
        self.PARM_DOUBLE_TYPE = _vsp.PARM_DOUBLE_TYPE
        self.PARM_INT_TYPE = _vsp.PARM_INT_TYPE
        self.PARM_BOOL_TYPE = _vsp.PARM_BOOL_TYPE
        self.PARM_FRACTION_TYPE = _vsp.PARM_FRACTION_TYPE
        self.PARM_LIMITED_INT_TYPE = _vsp.PARM_LIMITED_INT_TYPE
        self.PARM_NOTEQ_TYPE = _vsp.PARM_NOTEQ_TYPE
        self.PARM_POWER_INT_TYPE = _vsp.PARM_POWER_INT_TYPE
        self.PATCH_NONE = _vsp.PATCH_NONE
        self.PATCH_POINT = _vsp.PATCH_POINT
        self.PATCH_LINE = _vsp.PATCH_LINE
        self.PATCH_COPY = _vsp.PATCH_COPY
        self.PATCH_HALFWAY = _vsp.PATCH_HALFWAY
        self.PATCH_NUM_TYPES = _vsp.PATCH_NUM_TYPES
        self.LINEAR = _vsp.LINEAR
        self.PCHIP = _vsp.PCHIP
        self.CEDIT = _vsp.CEDIT
        self.APPROX_CEDIT = _vsp.APPROX_CEDIT
        self.NUM_PCURV_TYPE = _vsp.NUM_PCURV_TYPE
        self.PRES_UNIT_PSF = _vsp.PRES_UNIT_PSF
        self.PRES_UNIT_PSI = _vsp.PRES_UNIT_PSI
        self.PRES_UNIT_BA = _vsp.PRES_UNIT_BA
        self.PRES_UNIT_PA = _vsp.PRES_UNIT_PA
        self.PRES_UNIT_KPA = _vsp.PRES_UNIT_KPA
        self.PRES_UNIT_MPA = _vsp.PRES_UNIT_MPA
        self.PRES_UNIT_INCHHG = _vsp.PRES_UNIT_INCHHG
        self.PRES_UNIT_MMHG = _vsp.PRES_UNIT_MMHG
        self.PRES_UNIT_MMH20 = _vsp.PRES_UNIT_MMH20
        self.PRES_UNIT_MB = _vsp.PRES_UNIT_MB
        self.PRES_UNIT_ATM = _vsp.PRES_UNIT_ATM
        self.NUM_PRES_UNIT = _vsp.NUM_PRES_UNIT
        self.NO_BOUNDARY = _vsp.NO_BOUNDARY
        self.SET_BOUNDARY = _vsp.SET_BOUNDARY
        self.GEOM_BOUNDARY = _vsp.GEOM_BOUNDARY
        self.NUM_PROJ_BNDY_OPTIONS = _vsp.NUM_PROJ_BNDY_OPTIONS
        self.X_PROJ = _vsp.X_PROJ
        self.Y_PROJ = _vsp.Y_PROJ
        self.Z_PROJ = _vsp.Z_PROJ
        self.GEOM_PROJ = _vsp.GEOM_PROJ
        self.VEC_PROJ = _vsp.VEC_PROJ
        self.NUM_PROJ_DIR_OPTIONS = _vsp.NUM_PROJ_DIR_OPTIONS
        self.SET_TARGET = _vsp.SET_TARGET
        self.GEOM_TARGET = _vsp.GEOM_TARGET
        self.MODE_TARGET = _vsp.MODE_TARGET
        self.Z_TARGET = _vsp.Z_TARGET
        self.XYZ_TARGET = _vsp.XYZ_TARGET
        self.NUM_PROJ_TGT_OPTIONS = _vsp.NUM_PROJ_TGT_OPTIONS
        self.PROP_AZI_UNIFORM = _vsp.PROP_AZI_UNIFORM
        self.PROP_AZI_FREE = _vsp.PROP_AZI_FREE
        self.PROP_AZI_BALANCED = _vsp.PROP_AZI_BALANCED
        self.NUM_PROP_AZI = _vsp.NUM_PROP_AZI
        self.PROP_BLADE_TRADITIONAL = _vsp.PROP_BLADE_TRADITIONAL
        self.PROP_BLADE_NONTRADITIONAL = _vsp.PROP_BLADE_NONTRADITIONAL
        self.NUM_PROP_BLADE_MODES = _vsp.NUM_PROP_BLADE_MODES
        self.RPM_PROP_DRIVER = _vsp.RPM_PROP_DRIVER
        self.CT_PROP_DRIVER = _vsp.CT_PROP_DRIVER
        self.CP_PROP_DRIVER = _vsp.CP_PROP_DRIVER
        self.T_PROP_DRIVER = _vsp.T_PROP_DRIVER
        self.ETA_PROP_DRIVER = _vsp.ETA_PROP_DRIVER
        self.J_PROP_DRIVER = _vsp.J_PROP_DRIVER
        self.P_PROP_DRIVER = _vsp.P_PROP_DRIVER
        self.CQ_PROP_DRIVER = _vsp.CQ_PROP_DRIVER
        self.Q_PROP_DRIVER = _vsp.Q_PROP_DRIVER
        self.NUM_PROP_DRIVER = _vsp.NUM_PROP_DRIVER
        self.PROP_BLADES = _vsp.PROP_BLADES
        self.PROP_BOTH = _vsp.PROP_BOTH
        self.PROP_DISK = _vsp.PROP_DISK
        self.PROP_CHORD = _vsp.PROP_CHORD
        self.PROP_TWIST = _vsp.PROP_TWIST
        self.PROP_RAKE = _vsp.PROP_RAKE
        self.PROP_SKEW = _vsp.PROP_SKEW
        self.PROP_SWEEP = _vsp.PROP_SWEEP
        self.PROP_THICK = _vsp.PROP_THICK
        self.PROP_CLI = _vsp.PROP_CLI
        self.PROP_AXIAL = _vsp.PROP_AXIAL
        self.PROP_TANGENTIAL = _vsp.PROP_TANGENTIAL
        self.NUM_PROP_PCURVE = _vsp.NUM_PROP_PCURVE
        self.REORDER_MOVE_UP = _vsp.REORDER_MOVE_UP
        self.REORDER_MOVE_DOWN = _vsp.REORDER_MOVE_DOWN
        self.REORDER_MOVE_TOP = _vsp.REORDER_MOVE_TOP
        self.REORDER_MOVE_BOTTOM = _vsp.REORDER_MOVE_BOTTOM
        self.NUM_REORDER_TYPES = _vsp.NUM_REORDER_TYPES
        self.MANUAL_REF = _vsp.MANUAL_REF
        self.COMPONENT_REF = _vsp.COMPONENT_REF
        self.NUM_REF_TYPES = _vsp.NUM_REF_TYPES
        self.INVALID_TYPE = _vsp.INVALID_TYPE
        self.BOOL_DATA = _vsp.BOOL_DATA
        self.INT_DATA = _vsp.INT_DATA
        self.DOUBLE_DATA = _vsp.DOUBLE_DATA
        self.STRING_DATA = _vsp.STRING_DATA
        self.VEC3D_DATA = _vsp.VEC3D_DATA
        self.INT_MATRIX_DATA = _vsp.INT_MATRIX_DATA
        self.DOUBLE_MATRIX_DATA = _vsp.DOUBLE_MATRIX_DATA
        self.ATTR_COLLECTION_DATA = _vsp.ATTR_COLLECTION_DATA
        self.PARM_REFERENCE_DATA = _vsp.PARM_REFERENCE_DATA
        self.MESH_INDEXED_TRI = _vsp.MESH_INDEXED_TRI
        self.MESH_SLICE_TRI = _vsp.MESH_SLICE_TRI
        self.GEOM_XSECS = _vsp.GEOM_XSECS
        self.MESH_INDEX_AND_SLICE_TRI = _vsp.MESH_INDEX_AND_SLICE_TRI
        self.RHO_UNIT_SLUG_FT3 = _vsp.RHO_UNIT_SLUG_FT3
        self.RHO_UNIT_G_CM3 = _vsp.RHO_UNIT_G_CM3
        self.RHO_UNIT_KG_M3 = _vsp.RHO_UNIT_KG_M3
        self.RHO_UNIT_TONNE_MM3 = _vsp.RHO_UNIT_TONNE_MM3
        self.RHO_UNIT_LBM_FT3 = _vsp.RHO_UNIT_LBM_FT3
        self.RHO_UNIT_LBFSEC2_IN4 = _vsp.RHO_UNIT_LBFSEC2_IN4
        self.RHO_UNIT_LBM_IN3 = _vsp.RHO_UNIT_LBM_IN3
        self.NUM_RHO_UNIT = _vsp.NUM_RHO_UNIT
        self.ROUTE_PT_COMP = _vsp.ROUTE_PT_COMP
        self.ROUTE_PT_UV = _vsp.ROUTE_PT_UV
        self.ROUTE_PT_RST = _vsp.ROUTE_PT_RST
        self.ROUTE_PT_LMN = _vsp.ROUTE_PT_LMN
        self.ROUTE_PT_EtaMN = _vsp.ROUTE_PT_EtaMN
        self.ROUTE_PT_NUM_TYPES = _vsp.ROUTE_PT_NUM_TYPES
        self.ROUTE_PT_DELTA_XYZ = _vsp.ROUTE_PT_DELTA_XYZ
        self.ROUTE_PT_DELTA_COMP = _vsp.ROUTE_PT_DELTA_COMP
        self.ROUTE_PT_DELTA_UVN = _vsp.ROUTE_PT_DELTA_UVN
        self.ROUTE_PT_DELTA_NUM_TYPES = _vsp.ROUTE_PT_DELTA_NUM_TYPES
        self.SCALE_WIDTH = _vsp.SCALE_WIDTH
        self.SCALE_HEIGHT = _vsp.SCALE_HEIGHT
        self.SCALE_WIDTH_HEIGHT = _vsp.SCALE_WIDTH_HEIGHT
        self.SCALE_RESOLUTION = _vsp.SCALE_RESOLUTION
        self.SCALE_WIDTH_AR = _vsp.SCALE_WIDTH_AR
        self.SCALE_HEIGHT_AR = _vsp.SCALE_HEIGHT_AR
        self.NUM_SCALE_TYPES = _vsp.NUM_SCALE_TYPES
        self.SET_NONE = _vsp.SET_NONE
        self.SET_ALL = _vsp.SET_ALL
        self.SET_SHOWN = _vsp.SET_SHOWN
        self.SET_NOT_SHOWN = _vsp.SET_NOT_SHOWN
        self.SET_FIRST_USER = _vsp.SET_FIRST_USER
        self.MIN_NUM_USER = _vsp.MIN_NUM_USER
        self.MAX_NUM_SETS = _vsp.MAX_NUM_SETS
        self.STACK_PRESET_DEFAULT = _vsp.STACK_PRESET_DEFAULT
        self.STACK_PRESET_CYLINDER_ENDPTS = _vsp.STACK_PRESET_CYLINDER_ENDPTS
        self.STACK_PRESET_CYLINDER_ENDCAPS = _vsp.STACK_PRESET_CYLINDER_ENDCAPS
        self.STACK_PRESET_FLOWTHRU_OUTLIP_ORIG = _vsp.STACK_PRESET_FLOWTHRU_OUTLIP_ORIG
        self.STACK_PRESET_FLOWTHRU_INLIP_ORIG = _vsp.STACK_PRESET_FLOWTHRU_INLIP_ORIG
        self.STACK_PRESET_FLOWTHRU_MID_ORIG = _vsp.STACK_PRESET_FLOWTHRU_MID_ORIG
        self.STACK_PRESET_BOTHFACEFACE = _vsp.STACK_PRESET_BOTHFACEFACE
        self.STACK_PRESET_BOTHLIPFACE = _vsp.STACK_PRESET_BOTHLIPFACE
        self.STACK_PRESET_BOTHFACELIP = _vsp.STACK_PRESET_BOTHFACELIP
        self.STACK_PRESET_BOTHLIPLIP = _vsp.STACK_PRESET_BOTHLIPLIP
        self.STACK_PRESET_OUTFACE = _vsp.STACK_PRESET_OUTFACE
        self.STACK_PRESET_OUTLIP = _vsp.STACK_PRESET_OUTLIP
        self.STACK_PRESET_INFACE = _vsp.STACK_PRESET_INFACE
        self.STACK_PRESET_INLIP = _vsp.STACK_PRESET_INLIP
        self.STACK_PRESET_FLOWPATH_BOTH = _vsp.STACK_PRESET_FLOWPATH_BOTH
        self.STACK_PRESET_FLOWPATH_IN = _vsp.STACK_PRESET_FLOWPATH_IN
        self.STACK_PRESET_FLOWPATH_OUT = _vsp.STACK_PRESET_FLOWPATH_OUT
        self.STEP_SHELL = _vsp.STEP_SHELL
        self.STEP_BREP = _vsp.STEP_BREP
        self.SS_INC_TREAT_AS_PARENT = _vsp.SS_INC_TREAT_AS_PARENT
        self.SS_INC_SEPARATE_TREATMENT = _vsp.SS_INC_SEPARATE_TREATMENT
        self.SS_INC_ZERO_DRAG = _vsp.SS_INC_ZERO_DRAG
        self.INSIDE = _vsp.INSIDE
        self.OUTSIDE = _vsp.OUTSIDE
        self.NONE = _vsp.NONE
        self.CONST_U = _vsp.CONST_U
        self.CONST_W = _vsp.CONST_W
        self.SS_LINE = _vsp.SS_LINE
        self.SS_RECTANGLE = _vsp.SS_RECTANGLE
        self.SS_ELLIPSE = _vsp.SS_ELLIPSE
        self.SS_CONTROL = _vsp.SS_CONTROL
        self.SS_LINE_ARRAY = _vsp.SS_LINE_ARRAY
        self.SS_FINITE_LINE = _vsp.SS_FINITE_LINE
        self.SS_XSEC_CURVE = _vsp.SS_XSEC_CURVE
        self.SS_INTERSECT = _vsp.SS_INTERSECT
        self.SS_NUM_TYPES = _vsp.SS_NUM_TYPES
        self.SYM_XY = _vsp.SYM_XY
        self.SYM_XZ = _vsp.SYM_XZ
        self.SYM_YZ = _vsp.SYM_YZ
        self.SYM_ROT_X = _vsp.SYM_ROT_X
        self.SYM_ROT_Y = _vsp.SYM_ROT_Y
        self.SYM_ROT_Z = _vsp.SYM_ROT_Z
        self.SYM_PLANAR_TYPES = _vsp.SYM_PLANAR_TYPES
        self.SYM_NUM_TYPES = _vsp.SYM_NUM_TYPES
        self.SYM_NONE = _vsp.SYM_NONE
        self.SYM_RL = _vsp.SYM_RL
        self.SYM_TB = _vsp.SYM_TB
        self.SYM_ALL = _vsp.SYM_ALL
        self.TEMP_UNIT_K = _vsp.TEMP_UNIT_K
        self.TEMP_UNIT_C = _vsp.TEMP_UNIT_C
        self.TEMP_UNIT_F = _vsp.TEMP_UNIT_F
        self.TEMP_UNIT_R = _vsp.TEMP_UNIT_R
        self.NUM_TEMP_UNIT = _vsp.NUM_TEMP_UNIT
        self.TIRE_NOMINAL_CLEARANCE = _vsp.TIRE_NOMINAL_CLEARANCE
        self.TIRE_GROWTH_CLEARANCE = _vsp.TIRE_GROWTH_CLEARANCE
        self.TIRE_CLEARANCE = _vsp.TIRE_CLEARANCE
        self.NUM_TIRE_CLEARANCE_MODES = _vsp.NUM_TIRE_CLEARANCE_MODES
        self.TIRE_STATIC_LODED_CONTACT = _vsp.TIRE_STATIC_LODED_CONTACT
        self.TIRE_NOMINAL_CONTACT = _vsp.TIRE_NOMINAL_CONTACT
        self.TIRE_GROWTH_CONTACT = _vsp.TIRE_GROWTH_CONTACT
        self.TIRE_FLAT_CONTACT = _vsp.TIRE_FLAT_CONTACT
        self.NUM_TIRE_CONTACT_MODES = _vsp.NUM_TIRE_CONTACT_MODES
        self.TIRE_DIM_IN = _vsp.TIRE_DIM_IN
        self.TIRE_DIM_MODEL = _vsp.TIRE_DIM_MODEL
        self.TIRE_DIM_FRAC = _vsp.TIRE_DIM_FRAC
        self.NUM_TIRE_DIM_MODES = _vsp.NUM_TIRE_DIM_MODES
        self.TIRE_TRA = _vsp.TIRE_TRA
        self.TIRE_FAIR_FLANGE = _vsp.TIRE_FAIR_FLANGE
        self.TIRE_FAIR_WHEEL = _vsp.TIRE_FAIR_WHEEL
        self.TIRE_BALLOON = _vsp.TIRE_BALLOON
        self.TIRE_BALLOON_WHEEL = _vsp.TIRE_BALLOON_WHEEL
        self.TIRE_BALLOON_FAIR_WHEEL = _vsp.TIRE_BALLOON_FAIR_WHEEL
        self.NUM_TIRE_MODES = _vsp.NUM_TIRE_MODES
        self.V_UNIT_FT_S = _vsp.V_UNIT_FT_S
        self.V_UNIT_M_S = _vsp.V_UNIT_M_S
        self.V_UNIT_MPH = _vsp.V_UNIT_MPH
        self.V_UNIT_KM_HR = _vsp.V_UNIT_KM_HR
        self.V_UNIT_KEAS = _vsp.V_UNIT_KEAS
        self.V_UNIT_KTAS = _vsp.V_UNIT_KTAS
        self.V_UNIT_MACH = _vsp.V_UNIT_MACH
        self.VIEW_1 = _vsp.VIEW_1
        self.VIEW_2HOR = _vsp.VIEW_2HOR
        self.VIEW_2VER = _vsp.VIEW_2VER
        self.VIEW_4 = _vsp.VIEW_4
        self.ROT_0 = _vsp.ROT_0
        self.ROT_90 = _vsp.ROT_90
        self.ROT_180 = _vsp.ROT_180
        self.ROT_270 = _vsp.ROT_270
        self.VIEW_LEFT = _vsp.VIEW_LEFT
        self.VIEW_RIGHT = _vsp.VIEW_RIGHT
        self.VIEW_TOP = _vsp.VIEW_TOP
        self.VIEW_BOTTOM = _vsp.VIEW_BOTTOM
        self.VIEW_FRONT = _vsp.VIEW_FRONT
        self.VIEW_REAR = _vsp.VIEW_REAR
        self.VIEW_NONE = _vsp.VIEW_NONE
        self.VIEW_NUM_TYPES = _vsp.VIEW_NUM_TYPES
        self.NOISE_FLYBY = _vsp.NOISE_FLYBY
        self.NOISE_FOOTPRINT = _vsp.NOISE_FOOTPRINT
        self.NOISE_STEADY = _vsp.NOISE_STEADY
        self.NOISE_SI = _vsp.NOISE_SI
        self.NOISE_ENGLISH = _vsp.NOISE_ENGLISH
        self.VSPAERO_PROP_STATIC = _vsp.VSPAERO_PROP_STATIC
        self.VSPAERO_PROP_UNSTEADY = _vsp.VSPAERO_PROP_UNSTEADY
        self.VSPAERO_PROP_PSEUDO_STEADY = _vsp.VSPAERO_PROP_PSEUDO_STEADY
        self.VSPAERO_PROP_NUM_MODES = _vsp.VSPAERO_PROP_NUM_MODES
        self.STABILITY_OFF = _vsp.STABILITY_OFF
        self.STABILITY_DEFAULT = _vsp.STABILITY_DEFAULT
        self.STABILITY_P_ANALYSIS = _vsp.STABILITY_P_ANALYSIS
        self.STABILITY_Q_ANALYSIS = _vsp.STABILITY_Q_ANALYSIS
        self.STABILITY_R_ANALYSIS = _vsp.STABILITY_R_ANALYSIS
        self.STABILITY_PITCH = _vsp.STABILITY_PITCH
        self.STABILITY_ADJOINT = _vsp.STABILITY_ADJOINT
        self.STABILITY_NUM_TYPES = _vsp.STABILITY_NUM_TYPES
        self.STALL_OFF = _vsp.STALL_OFF
        self.STALL_ON = _vsp.STALL_ON
        self.CFD_NORMAL = _vsp.CFD_NORMAL
        self.CFD_NEGATIVE = _vsp.CFD_NEGATIVE
        self.CFD_TRANSPARENT = _vsp.CFD_TRANSPARENT
        self.CFD_STRUCTURE = _vsp.CFD_STRUCTURE
        self.CFD_STIFFENER = _vsp.CFD_STIFFENER
        self.CFD_MEASURE_DUCT = _vsp.CFD_MEASURE_DUCT
        self.CFD_NUM_TYPES = _vsp.CFD_NUM_TYPES
        self.NORMAL_SURF = _vsp.NORMAL_SURF
        self.WING_SURF = _vsp.WING_SURF
        self.DISK_SURF = _vsp.DISK_SURF
        self.NUM_SURF_TYPES = _vsp.NUM_SURF_TYPES
        self.WHEEL_TIRE_1LG = _vsp.WHEEL_TIRE_1LG
        self.WHEEL_TIRE_1SM = _vsp.WHEEL_TIRE_1SM
        self.WHEEL_TIRE_2 = _vsp.WHEEL_TIRE_2
        self.WHEEL_TIRE_3E = _vsp.WHEEL_TIRE_3E
        self.WHEEL_TIRE_3R = _vsp.WHEEL_TIRE_3R
        self.WHEEL_TIRE_4 = _vsp.WHEEL_TIRE_4
        self.NUM_WHEEL_TIRE_FAILURE_MODES = _vsp.NUM_WHEEL_TIRE_FAILURE_MODES
        self.W_RIGHT_0 = _vsp.W_RIGHT_0
        self.W_BOTTOM = _vsp.W_BOTTOM
        self.W_LEFT = _vsp.W_LEFT
        self.W_TOP = _vsp.W_TOP
        self.W_RIGHT_1 = _vsp.W_RIGHT_1
        self.W_FREE = _vsp.W_FREE
        self.BLEND_FREE = _vsp.BLEND_FREE
        self.BLEND_ANGLES = _vsp.BLEND_ANGLES
        self.BLEND_MATCH_IN_LE_TRAP = _vsp.BLEND_MATCH_IN_LE_TRAP
        self.BLEND_MATCH_IN_TE_TRAP = _vsp.BLEND_MATCH_IN_TE_TRAP
        self.BLEND_MATCH_OUT_LE_TRAP = _vsp.BLEND_MATCH_OUT_LE_TRAP
        self.BLEND_MATCH_OUT_TE_TRAP = _vsp.BLEND_MATCH_OUT_TE_TRAP
        self.BLEND_MATCH_IN_ANGLES = _vsp.BLEND_MATCH_IN_ANGLES
        self.BLEND_MATCH_LE_ANGLES = _vsp.BLEND_MATCH_LE_ANGLES
        self.BLEND_NUM_TYPES = _vsp.BLEND_NUM_TYPES
        self.AR_WSECT_DRIVER = _vsp.AR_WSECT_DRIVER
        self.SPAN_WSECT_DRIVER = _vsp.SPAN_WSECT_DRIVER
        self.AREA_WSECT_DRIVER = _vsp.AREA_WSECT_DRIVER
        self.TAPER_WSECT_DRIVER = _vsp.TAPER_WSECT_DRIVER
        self.AVEC_WSECT_DRIVER = _vsp.AVEC_WSECT_DRIVER
        self.ROOTC_WSECT_DRIVER = _vsp.ROOTC_WSECT_DRIVER
        self.TIPC_WSECT_DRIVER = _vsp.TIPC_WSECT_DRIVER
        self.SECSWEEP_WSECT_DRIVER = _vsp.SECSWEEP_WSECT_DRIVER
        self.NUM_WSECT_DRIVER = _vsp.NUM_WSECT_DRIVER
        self.SWEEP_WSECT_DRIVER = _vsp.SWEEP_WSECT_DRIVER
        self.SWEEPLOC_WSECT_DRIVER = _vsp.SWEEPLOC_WSECT_DRIVER
        self.SECSWEEPLOC_WSECT_DRIVER = _vsp.SECSWEEPLOC_WSECT_DRIVER
        self.XDDM_VAR = _vsp.XDDM_VAR
        self.XDDM_CONST = _vsp.XDDM_CONST
        self.CLOSE_NONE = _vsp.CLOSE_NONE
        self.CLOSE_SKEWLOW = _vsp.CLOSE_SKEWLOW
        self.CLOSE_SKEWUP = _vsp.CLOSE_SKEWUP
        self.CLOSE_SKEWBOTH = _vsp.CLOSE_SKEWBOTH
        self.CLOSE_EXTRAP = _vsp.CLOSE_EXTRAP
        self.CLOSE_NUM_TYPES = _vsp.CLOSE_NUM_TYPES
        self.XS_UNDEFINED = _vsp.XS_UNDEFINED
        self.XS_POINT = _vsp.XS_POINT
        self.XS_CIRCLE = _vsp.XS_CIRCLE
        self.XS_ELLIPSE = _vsp.XS_ELLIPSE
        self.XS_SUPER_ELLIPSE = _vsp.XS_SUPER_ELLIPSE
        self.XS_ROUNDED_RECTANGLE = _vsp.XS_ROUNDED_RECTANGLE
        self.XS_GENERAL_FUSE = _vsp.XS_GENERAL_FUSE
        self.XS_FILE_FUSE = _vsp.XS_FILE_FUSE
        self.XS_FOUR_SERIES = _vsp.XS_FOUR_SERIES
        self.XS_SIX_SERIES = _vsp.XS_SIX_SERIES
        self.XS_BICONVEX = _vsp.XS_BICONVEX
        self.XS_WEDGE = _vsp.XS_WEDGE
        self.XS_EDIT_CURVE = _vsp.XS_EDIT_CURVE
        self.XS_FILE_AIRFOIL = _vsp.XS_FILE_AIRFOIL
        self.XS_CST_AIRFOIL = _vsp.XS_CST_AIRFOIL
        self.XS_VKT_AIRFOIL = _vsp.XS_VKT_AIRFOIL
        self.XS_FOUR_DIGIT_MOD = _vsp.XS_FOUR_DIGIT_MOD
        self.XS_FIVE_DIGIT = _vsp.XS_FIVE_DIGIT
        self.XS_FIVE_DIGIT_MOD = _vsp.XS_FIVE_DIGIT_MOD
        self.XS_ONE_SIX_SERIES = _vsp.XS_ONE_SIX_SERIES
        self.XS_AC25_773 = _vsp.XS_AC25_773
        self.XS_NUM_TYPES = _vsp.XS_NUM_TYPES
        self.WIDTH_XSEC_DRIVER = _vsp.WIDTH_XSEC_DRIVER
        self.AREA_XSEC_DRIVER = _vsp.AREA_XSEC_DRIVER
        self.HEIGHT_XSEC_DRIVER = _vsp.HEIGHT_XSEC_DRIVER
        self.HWRATIO_XSEC_DRIVER = _vsp.HWRATIO_XSEC_DRIVER
        self.NUM_XSEC_DRIVER = _vsp.NUM_XSEC_DRIVER
        self.CIRCLE_NUM_XSEC_DRIVER = _vsp.CIRCLE_NUM_XSEC_DRIVER
        self.FLAP_NONE = _vsp.FLAP_NONE
        self.FLAP_PLAIN = _vsp.FLAP_PLAIN
        self.FLAP_NUM_TYPES = _vsp.FLAP_NUM_TYPES
        self.XSEC_BOTH_SIDES = _vsp.XSEC_BOTH_SIDES
        self.XSEC_LEFT_SIDE = _vsp.XSEC_LEFT_SIDE
        self.XSEC_RIGHT_SIDE = _vsp.XSEC_RIGHT_SIDE
        self.TRIM_NONE = _vsp.TRIM_NONE
        self.TRIM_X = _vsp.TRIM_X
        self.TRIM_THICK = _vsp.TRIM_THICK
        self.TRIM_NUM_TYPES = _vsp.TRIM_NUM_TYPES
        self.XSEC_FUSE = _vsp.XSEC_FUSE
        self.XSEC_STACK = _vsp.XSEC_STACK
        self.XSEC_WING = _vsp.XSEC_WING
        self.XSEC_CUSTOM = _vsp.XSEC_CUSTOM
        self.XSEC_PROP = _vsp.XSEC_PROP
        self.XSEC_NUM_TYPES = _vsp.XSEC_NUM_TYPES
        self.XS_SHIFT_LE = _vsp.XS_SHIFT_LE
        self.XS_SHIFT_MID = _vsp.XS_SHIFT_MID
        self.XS_SHIFT_TE = _vsp.XS_SHIFT_TE
        self.cvar = _vsp.cvar
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        # Register ErrorObj in _vsp:
        # Register ErrorMgrSingleton in _vsp:
        
    @client_wrap
    def VSPCheckSetup(self, ):
        r"""VSPCheckSetup()"""
        return _vsp.VSPCheckSetup()
    
    @client_wrap
    def VSPRenew(self, ):
        r"""VSPRenew()"""
        return _vsp.VSPRenew()
    
    @client_wrap
    def Update(self, update_managers=True):
        r"""Update(bool update_managers=True)"""
        return _vsp.Update(update_managers)
    
    @client_wrap
    def VSPExit(self, error_code):
        r"""VSPExit(int error_code)"""
        return _vsp.VSPExit(error_code)
    
    @client_wrap
    def VSPCrash(self, crash_type):
        r"""VSPCrash(int crash_type)"""
        return _vsp.VSPCrash(crash_type)
    
    @client_wrap
    def GetAndResetUpdateCount(self, ):
        r"""GetAndResetUpdateCount() -> int"""
        return _vsp.GetAndResetUpdateCount()
    
    @client_wrap
    def GetVSPVersion(self, ):
        r"""GetVSPVersion() -> std::string"""
        return _vsp.GetVSPVersion()
    
    @client_wrap
    def GetVSPVersionMajor(self, ):
        r"""GetVSPVersionMajor() -> int"""
        return _vsp.GetVSPVersionMajor()
    
    @client_wrap
    def GetVSPVersionMinor(self, ):
        r"""GetVSPVersionMinor() -> int"""
        return _vsp.GetVSPVersionMinor()
    
    @client_wrap
    def GetVSPVersionChange(self, ):
        r"""GetVSPVersionChange() -> int"""
        return _vsp.GetVSPVersionChange()
    
    @client_wrap
    def GetVSPExePath(self, ):
        r"""GetVSPExePath() -> std::string"""
        return _vsp.GetVSPExePath()
    
    @client_wrap
    def SetVSPAEROPath(self, path):
        r"""SetVSPAEROPath(std::string const & path) -> bool"""
        return _vsp.SetVSPAEROPath(path)
    
    @client_wrap
    def GetVSPAEROPath(self, ):
        r"""GetVSPAEROPath() -> std::string"""
        return _vsp.GetVSPAEROPath()
    
    @client_wrap
    def CheckForVSPAERO(self, path):
        r"""CheckForVSPAERO(std::string const & path) -> bool"""
        return _vsp.CheckForVSPAERO(path)
    
    @client_wrap
    def SetVSPHelpPath(self, path):
        r"""SetVSPHelpPath(std::string const & path) -> bool"""
        return _vsp.SetVSPHelpPath(path)
    
    @client_wrap
    def GetVSPHelpPath(self, ):
        r"""GetVSPHelpPath() -> std::string"""
        return _vsp.GetVSPHelpPath()
    
    @client_wrap
    def CheckForVSPHelp(self, path):
        r"""CheckForVSPHelp(std::string const & path) -> bool"""
        return _vsp.CheckForVSPHelp(path)
    
    @client_wrap
    def RegisterCFDMeshAnalyses(self, ):
        r"""RegisterCFDMeshAnalyses()"""
        return _vsp.RegisterCFDMeshAnalyses()
    
    @client_wrap
    def LimitedIntersectSurfaces(self, geomvec, ptchains, uwchains):
        r"""LimitedIntersectSurfaces(StringVector geomvec, std::vector< std::vector< vec3d,std::allocator< vec3d > >,std::allocator< std::vector< vec3d,std::allocator< vec3d > > > > & ptchains, std::vector< std::vector< vec3d,std::allocator< vec3d > >,std::allocator< std::vector< vec3d,std::allocator< vec3d > > > > & uwchains)"""
        return _vsp.LimitedIntersectSurfaces(geomvec, ptchains, uwchains)
    
    @client_wrap
    def ReadVSPFile(self, file_name):
        r"""ReadVSPFile(std::string const & file_name)"""
        return _vsp.ReadVSPFile(file_name)
    
    @client_wrap
    def WriteVSPFile(self, *args):
        r"""WriteVSPFile(std::string const & file_name, int set=SET_ALL)"""
        return _vsp.WriteVSPFile(*args)
    
    @client_wrap
    def SetVSP3FileName(self, file_name):
        r"""SetVSP3FileName(std::string const & file_name)"""
        return _vsp.SetVSP3FileName(file_name)
    
    @client_wrap
    def GetVSPFileName(self, ):
        r"""GetVSPFileName() -> std::string"""
        return _vsp.GetVSPFileName()
    
    @client_wrap
    def ClearVSPModel(self, ):
        r"""ClearVSPModel()"""
        return _vsp.ClearVSPModel()
    
    @client_wrap
    def InsertVSPFile(self, file_name, parent_geom_id):
        r"""InsertVSPFile(std::string const & file_name, std::string const & parent_geom_id)"""
        return _vsp.InsertVSPFile(file_name, parent_geom_id)
    
    @client_wrap
    def ExportFile(self, *args):
        r"""ExportFile(std::string const & file_name, int thick_set, int file_type, int subsFlag=1, int thin_set=SET_NONE, bool useMode=False, std::string const & modeID="") -> std::string"""
        return _vsp.ExportFile(*args)
    
    @client_wrap
    def ImportFile(self, file_name, file_type, parent):
        r"""ImportFile(std::string const & file_name, int file_type, std::string const & parent) -> std::string"""
        return _vsp.ImportFile(file_name, file_type, parent)
    
    @client_wrap
    def GetBEMPropID(self, ):
        r"""GetBEMPropID() -> std::string"""
        return _vsp.GetBEMPropID()
    
    @client_wrap
    def SetBEMPropID(self, prop_id):
        r"""SetBEMPropID(std::string const & prop_id)"""
        return _vsp.SetBEMPropID(prop_id)
    
    @client_wrap
    def ReadApplyDESFile(self, file_name):
        r"""ReadApplyDESFile(std::string const & file_name)"""
        return _vsp.ReadApplyDESFile(file_name)
    
    @client_wrap
    def WriteDESFile(self, file_name):
        r"""WriteDESFile(std::string const & file_name)"""
        return _vsp.WriteDESFile(file_name)
    
    @client_wrap
    def ReadApplyXDDMFile(self, file_name):
        r"""ReadApplyXDDMFile(std::string const & file_name)"""
        return _vsp.ReadApplyXDDMFile(file_name)
    
    @client_wrap
    def WriteXDDMFile(self, file_name):
        r"""WriteXDDMFile(std::string const & file_name)"""
        return _vsp.WriteXDDMFile(file_name)
    
    @client_wrap
    def GetNumDesignVars(self, ):
        r"""GetNumDesignVars() -> int"""
        return _vsp.GetNumDesignVars()
    
    @client_wrap
    def AddDesignVar(self, parm_id, type):
        r"""AddDesignVar(std::string const & parm_id, int type)"""
        return _vsp.AddDesignVar(parm_id, type)
    
    @client_wrap
    def DeleteAllDesignVars(self, ):
        r"""DeleteAllDesignVars()"""
        return _vsp.DeleteAllDesignVars()
    
    @client_wrap
    def GetDesignVar(self, index):
        r"""GetDesignVar(int index) -> std::string"""
        return _vsp.GetDesignVar(index)
    
    @client_wrap
    def GetDesignVarType(self, index):
        r"""GetDesignVarType(int index) -> int"""
        return _vsp.GetDesignVarType(index)
    
    @client_wrap
    def GetComputationFileName(self, file_type):
        r"""GetComputationFileName(int file_type) -> std::string"""
        return _vsp.GetComputationFileName(file_type)
    
    @client_wrap
    def SetComputationFileName(self, file_type, file_name):
        r"""SetComputationFileName(int file_type, std::string const & file_name)"""
        return _vsp.SetComputationFileName(file_type, file_name)
    
    @client_wrap
    def ComputeMassProps(self, set, num_slices, idir):
        r"""ComputeMassProps(int set, int num_slices, int idir) -> std::string"""
        return _vsp.ComputeMassProps(set, num_slices, idir)
    
    @client_wrap
    def ComputeCompGeom(self, set, half_mesh, file_export_types):
        r"""ComputeCompGeom(int set, bool half_mesh, int file_export_types) -> std::string"""
        return _vsp.ComputeCompGeom(set, half_mesh, file_export_types)
    
    @client_wrap
    def ComputePlaneSlice(self, set, num_slices, norm, auto_bnd, start_bnd=0, end_bnd=0, measureduct=False):
        r"""ComputePlaneSlice(int set, int num_slices, vec3d norm, bool auto_bnd, double start_bnd=0, double end_bnd=0, bool measureduct=False) -> std::string"""
        return _vsp.ComputePlaneSlice(set, num_slices, norm, auto_bnd, start_bnd, end_bnd, measureduct)
    
    @client_wrap
    def ComputeDegenGeom(self, set, file_export_types):
        r"""ComputeDegenGeom(int set, int file_export_types)"""
        return _vsp.ComputeDegenGeom(set, file_export_types)
    
    @client_wrap
    def ComputeCFDMesh(self, set, degenset, file_export_types):
        r"""ComputeCFDMesh(int set, int degenset, int file_export_types)"""
        return _vsp.ComputeCFDMesh(set, degenset, file_export_types)
    
    @client_wrap
    def GetCFDMeshVal(self, type):
        r"""GetCFDMeshVal(int type) -> double"""
        return _vsp.GetCFDMeshVal(type)
    
    @client_wrap
    def SetCFDMeshVal(self, type, val):
        r"""SetCFDMeshVal(int type, double val)"""
        return _vsp.SetCFDMeshVal(type, val)
    
    @client_wrap
    def GetCFDWakeFlag(self, geom_id):
        r"""GetCFDWakeFlag(std::string const & geom_id) -> bool"""
        return _vsp.GetCFDWakeFlag(geom_id)
    
    @client_wrap
    def SetCFDWakeFlag(self, geom_id, flag):
        r"""SetCFDWakeFlag(std::string const & geom_id, bool flag)"""
        return _vsp.SetCFDWakeFlag(geom_id, flag)
    
    @client_wrap
    def GetNumCFDSources(self, geom_id):
        r"""GetNumCFDSources(std::string const & geom_id) -> int"""
        return _vsp.GetNumCFDSources(geom_id)
    
    @client_wrap
    def GetCFDSourceName(self, geom_id, source_index):
        r"""GetCFDSourceName(std::string const & geom_id, int source_index) -> std::string"""
        return _vsp.GetCFDSourceName(geom_id, source_index)
    
    @client_wrap
    def SetCFDSourceName(self, geom_id, source_index, name):
        r"""SetCFDSourceName(std::string const & geom_id, int source_index, std::string const & name)"""
        return _vsp.SetCFDSourceName(geom_id, source_index, name)
    
    @client_wrap
    def GetCFDSourceType(self, geom_id, source_index):
        r"""GetCFDSourceType(std::string const & geom_id, int source_index) -> int"""
        return _vsp.GetCFDSourceType(geom_id, source_index)
    
    @client_wrap
    def DeleteCFDSource(self, geom_id, source_index):
        r"""DeleteCFDSource(std::string const & geom_id, int source_index)"""
        return _vsp.DeleteCFDSource(geom_id, source_index)
    
    @client_wrap
    def DeleteAllCFDSources(self, ):
        r"""DeleteAllCFDSources()"""
        return _vsp.DeleteAllCFDSources()
    
    @client_wrap
    def GetCFDSourceID(self, geom_id, source_index):
        r"""GetCFDSourceID(std::string const & geom_id, int source_index) -> std::string"""
        return _vsp.GetCFDSourceID(geom_id, source_index)
    
    @client_wrap
    def AdjustAllCFDSourceLen(self, mult):
        r"""AdjustAllCFDSourceLen(double mult)"""
        return _vsp.AdjustAllCFDSourceLen(mult)
    
    @client_wrap
    def AdjustAllCFDSourceRad(self, mult):
        r"""AdjustAllCFDSourceRad(double mult)"""
        return _vsp.AdjustAllCFDSourceRad(mult)
    
    @client_wrap
    def AddDefaultSources(self, ):
        r"""AddDefaultSources()"""
        return _vsp.AddDefaultSources()
    
    @client_wrap
    def AddCFDSource(self, type, geom_id, surf_index, l1, r1, u1, w1, l2=0, r2=0, u2=0, w2=0):
        r"""AddCFDSource(int type, std::string const & geom_id, int surf_index, double l1, double r1, double u1, double w1, double l2=0, double r2=0, double u2=0, double w2=0)"""
        return _vsp.AddCFDSource(type, geom_id, surf_index, l1, r1, u1, w1, l2, r2, u2, w2)
    
    @client_wrap
    def GetVSPAERORefWingID(self, ):
        r"""GetVSPAERORefWingID() -> std::string"""
        return _vsp.GetVSPAERORefWingID()
    
    @client_wrap
    def SetVSPAERORefWingID(self, geom_id):
        r"""SetVSPAERORefWingID(std::string const & geom_id) -> std::string"""
        return _vsp.SetVSPAERORefWingID(geom_id)
    
    @client_wrap
    def GetNumAnalysis(self, ):
        r"""GetNumAnalysis() -> int"""
        return _vsp.GetNumAnalysis()
    
    @client_wrap
    def ListAnalysis(self, ):
        r"""ListAnalysis() -> StringVector"""
        return _vsp.ListAnalysis()
    
    @client_wrap
    def GetAnalysisInputNames(self, analysis):
        r"""GetAnalysisInputNames(std::string const & analysis) -> StringVector"""
        return _vsp.GetAnalysisInputNames(analysis)
    
    @client_wrap
    def GetAnalysisDoc(self, analysis):
        r"""GetAnalysisDoc(std::string const & analysis) -> std::string"""
        return _vsp.GetAnalysisDoc(analysis)
    
    @client_wrap
    def GetAnalysisInputDoc(self, analysis, name):
        r"""GetAnalysisInputDoc(std::string const & analysis, std::string const & name) -> std::string"""
        return _vsp.GetAnalysisInputDoc(analysis, name)
    
    @client_wrap
    def ExecAnalysis(self, analysis):
        r"""ExecAnalysis(std::string const & analysis) -> std::string"""
        return _vsp.ExecAnalysis(analysis)
    
    @client_wrap
    def GetNumAnalysisInputData(self, analysis, name):
        r"""GetNumAnalysisInputData(std::string const & analysis, std::string const & name) -> int"""
        return _vsp.GetNumAnalysisInputData(analysis, name)
    
    @client_wrap
    def GetAnalysisInputType(self, analysis, name):
        r"""GetAnalysisInputType(std::string const & analysis, std::string const & name) -> int"""
        return _vsp.GetAnalysisInputType(analysis, name)
    
    @client_wrap
    def GetIntAnalysisInput(self, analysis, name, index=0):
        r"""GetIntAnalysisInput(std::string const & analysis, std::string const & name, int index=0) -> IntVector"""
        return _vsp.GetIntAnalysisInput(analysis, name, index)
    
    @client_wrap
    def GetDoubleAnalysisInput(self, analysis, name, index=0):
        r"""GetDoubleAnalysisInput(std::string const & analysis, std::string const & name, int index=0) -> DoubleVector"""
        return _vsp.GetDoubleAnalysisInput(analysis, name, index)
    
    @client_wrap
    def GetStringAnalysisInput(self, analysis, name, index=0):
        r"""GetStringAnalysisInput(std::string const & analysis, std::string const & name, int index=0) -> StringVector"""
        return _vsp.GetStringAnalysisInput(analysis, name, index)
    
    @client_wrap
    def GetVec3dAnalysisInput(self, analysis, name, index=0):
        r"""GetVec3dAnalysisInput(std::string const & analysis, std::string const & name, int index=0) -> Vec3dVec"""
        return _vsp.GetVec3dAnalysisInput(analysis, name, index)
    
    @client_wrap
    def SetAnalysisInputDefaults(self, analysis):
        r"""SetAnalysisInputDefaults(std::string const & analysis)"""
        return _vsp.SetAnalysisInputDefaults(analysis)
    
    @client_wrap
    def SetIntAnalysisInput(self, analysis, name, indata, index=0):
        r"""SetIntAnalysisInput(std::string const & analysis, std::string const & name, IntVector indata, int index=0)"""
        return _vsp.SetIntAnalysisInput(analysis, name, indata, index)
    
    @client_wrap
    def SetDoubleAnalysisInput(self, analysis, name, indata, index=0):
        r"""SetDoubleAnalysisInput(std::string const & analysis, std::string const & name, DoubleVector indata, int index=0)"""
        return _vsp.SetDoubleAnalysisInput(analysis, name, indata, index)
    
    @client_wrap
    def SetStringAnalysisInput(self, analysis, name, indata, index=0):
        r"""SetStringAnalysisInput(std::string const & analysis, std::string const & name, StringVector indata, int index=0)"""
        return _vsp.SetStringAnalysisInput(analysis, name, indata, index)
    
    @client_wrap
    def SetVec3dAnalysisInput(self, analysis, name, indata, index=0):
        r"""SetVec3dAnalysisInput(std::string const & analysis, std::string const & name, Vec3dVec indata, int index=0)"""
        return _vsp.SetVec3dAnalysisInput(analysis, name, indata, index)
    
    @client_wrap
    def PrintAnalysisInputs(self, analysis_name):
        r"""PrintAnalysisInputs(std::string const & analysis_name)"""
        return _vsp.PrintAnalysisInputs(analysis_name)
    
    @client_wrap
    def PrintAnalysisDocs(self, analysis_name):
        r"""PrintAnalysisDocs(std::string const & analysis_name)"""
        return _vsp.PrintAnalysisDocs(analysis_name)
    
    @client_wrap
    def AddGeometryAnalysis(self, ):
        r"""AddGeometryAnalysis() -> std::string"""
        return _vsp.AddGeometryAnalysis()
    
    @client_wrap
    def AddGeometryAnalysisAzEl(self, geom_analysis_id, azimuth, elevation):
        r"""AddGeometryAnalysisAzEl(std::string const & geom_analysis_id, double azimuth, double elevation)"""
        return _vsp.AddGeometryAnalysisAzEl(geom_analysis_id, azimuth, elevation)
    
    @client_wrap
    def DeleteGeometryAnalysisAzEl(self, geom_analysis_id, index):
        r"""DeleteGeometryAnalysisAzEl(std::string const & geom_analysis_id, int index)"""
        return _vsp.DeleteGeometryAnalysisAzEl(geom_analysis_id, index)
    
    @client_wrap
    def DeleteAllGeometryAnalysisAzEl(self, geom_analysis_id):
        r"""DeleteAllGeometryAnalysisAzEl(std::string const & geom_analysis_id)"""
        return _vsp.DeleteAllGeometryAnalysisAzEl(geom_analysis_id)
    
    @client_wrap
    def GetNumGeometryAnalysisAzEl(self, geom_analysis_id):
        r"""GetNumGeometryAnalysisAzEl(std::string const & geom_analysis_id) -> int"""
        return _vsp.GetNumGeometryAnalysisAzEl(geom_analysis_id)
    
    @client_wrap
    def GetGeometryAnalysisAzimuthParm(self, geom_analysis_id, index):
        r"""GetGeometryAnalysisAzimuthParm(std::string const & geom_analysis_id, int index) -> std::string"""
        return _vsp.GetGeometryAnalysisAzimuthParm(geom_analysis_id, index)
    
    @client_wrap
    def GetGeometryAnalysisElevationParm(self, geom_analysis_id, index):
        r"""GetGeometryAnalysisElevationParm(std::string const & geom_analysis_id, int index) -> std::string"""
        return _vsp.GetGeometryAnalysisElevationParm(geom_analysis_id, index)
    
    @client_wrap
    def DeleteGeometryAnalysis(self, id):
        r"""DeleteGeometryAnalysis(std::string const & id)"""
        return _vsp.DeleteGeometryAnalysis(id)
    
    @client_wrap
    def DeleteAllGeometryAnalyses(self, ):
        r"""DeleteAllGeometryAnalyses()"""
        return _vsp.DeleteAllGeometryAnalyses()
    
    @client_wrap
    def GetAllGeometryAnalysesIDVec(self, ):
        r"""GetAllGeometryAnalysesIDVec() -> StringVector"""
        return _vsp.GetAllGeometryAnalysesIDVec()
    
    @client_wrap
    def SetActiveGeometryAnalysis(self, id):
        r"""SetActiveGeometryAnalysis(std::string const & id)"""
        return _vsp.SetActiveGeometryAnalysis(id)
    
    @client_wrap
    def GetActiveGeometryAnalysis(self, ):
        r"""GetActiveGeometryAnalysis() -> std::string"""
        return _vsp.GetActiveGeometryAnalysis()
    
    @client_wrap
    def MakeMeshGeom(self, ga_id):
        r"""MakeMeshGeom(std::string const & ga_id) -> std::string"""
        return _vsp.MakeMeshGeom(ga_id)
    
    @client_wrap
    def SummarizeAttributes(self, ):
        r"""SummarizeAttributes() -> std::string"""
        return _vsp.SummarizeAttributes()
    
    @client_wrap
    def SummarizeAttributesAsTree(self, ):
        r"""SummarizeAttributesAsTree() -> std::string"""
        return _vsp.SummarizeAttributesAsTree()
    
    @client_wrap
    def FindAllAttributes(self, ):
        r"""FindAllAttributes() -> StringVector"""
        return _vsp.FindAllAttributes()
    
    @client_wrap
    def FindAttributesByName(self, search_str):
        r"""FindAttributesByName(std::string const & search_str) -> StringVector"""
        return _vsp.FindAttributesByName(search_str)
    
    @client_wrap
    def FindAttributeByName(self, search_str, index):
        r"""FindAttributeByName(std::string const & search_str, int index) -> std::string"""
        return _vsp.FindAttributeByName(search_str, index)
    
    @client_wrap
    def FindAttributeInCollection(self, obj_id, search_str, index):
        r"""FindAttributeInCollection(std::string const & obj_id, std::string const & search_str, int index) -> std::string"""
        return _vsp.FindAttributeInCollection(obj_id, search_str, index)
    
    @client_wrap
    def FindAttributeNamesInCollection(self, collID):
        r"""FindAttributeNamesInCollection(std::string const & collID) -> StringVector"""
        return _vsp.FindAttributeNamesInCollection(collID)
    
    @client_wrap
    def FindAttributesInCollection(self, collID):
        r"""FindAttributesInCollection(std::string const & collID) -> StringVector"""
        return _vsp.FindAttributesInCollection(collID)
    
    @client_wrap
    def FindAttributedObjects(self, ):
        r"""FindAttributedObjects() -> StringVector"""
        return _vsp.FindAttributedObjects()
    
    @client_wrap
    def GetObjectType(self, attachID):
        r"""GetObjectType(std::string const & attachID) -> int"""
        return _vsp.GetObjectType(attachID)
    
    @client_wrap
    def GetObjectTypeName(self, attachID):
        r"""GetObjectTypeName(std::string const & attachID) -> std::string"""
        return _vsp.GetObjectTypeName(attachID)
    
    @client_wrap
    def GetObjectName(self, attachID):
        r"""GetObjectName(std::string const & attachID) -> std::string"""
        return _vsp.GetObjectName(attachID)
    
    @client_wrap
    def GetObjectParent(self, id):
        r"""GetObjectParent(std::string const & id) -> std::string"""
        return _vsp.GetObjectParent(id)
    
    @client_wrap
    def GetChildCollection(self, attachID):
        r"""GetChildCollection(std::string const & attachID) -> std::string"""
        return _vsp.GetChildCollection(attachID)
    
    @client_wrap
    def GetGeomSetCollection(self, index):
        r"""GetGeomSetCollection(int const & index) -> std::string"""
        return _vsp.GetGeomSetCollection(index)
    
    @client_wrap
    def GetAttributeName(self, attrID):
        r"""GetAttributeName(std::string const & attrID) -> std::string"""
        return _vsp.GetAttributeName(attrID)
    
    @client_wrap
    def GetAttributeID(self, collID, attributeName, index):
        r"""GetAttributeID(std::string const & collID, std::string const & attributeName, int index) -> std::string"""
        return _vsp.GetAttributeID(collID, attributeName, index)
    
    @client_wrap
    def GetAttributeDoc(self, attrID):
        r"""GetAttributeDoc(std::string const & attrID) -> std::string"""
        return _vsp.GetAttributeDoc(attrID)
    
    @client_wrap
    def GetAttributeType(self, attrID):
        r"""GetAttributeType(std::string const & attrID) -> int"""
        return _vsp.GetAttributeType(attrID)
    
    @client_wrap
    def GetAttributeTypeName(self, attrID):
        r"""GetAttributeTypeName(std::string const & attrID) -> std::string"""
        return _vsp.GetAttributeTypeName(attrID)
    
    @client_wrap
    def GetAttributeBoolVal(self, attrID):
        r"""GetAttributeBoolVal(std::string const & attrID) -> IntVector"""
        return _vsp.GetAttributeBoolVal(attrID)
    
    @client_wrap
    def GetAttributeIntVal(self, attrID):
        r"""GetAttributeIntVal(std::string const & attrID) -> IntVector"""
        return _vsp.GetAttributeIntVal(attrID)
    
    @client_wrap
    def GetAttributeDoubleVal(self, attrID):
        r"""GetAttributeDoubleVal(std::string const & attrID) -> DoubleVector"""
        return _vsp.GetAttributeDoubleVal(attrID)
    
    @client_wrap
    def GetAttributeStringVal(self, attrID):
        r"""GetAttributeStringVal(std::string const & attrID) -> StringVector"""
        return _vsp.GetAttributeStringVal(attrID)
    
    @client_wrap
    def GetAttributeParmID(self, attrID):
        r"""GetAttributeParmID(std::string const & attrID) -> StringVector"""
        return _vsp.GetAttributeParmID(attrID)
    
    @client_wrap
    def GetAttributeParmVal(self, attrID):
        r"""GetAttributeParmVal(std::string const & attrID) -> DoubleVector"""
        return _vsp.GetAttributeParmVal(attrID)
    
    @client_wrap
    def GetAttributeParmName(self, attrID):
        r"""GetAttributeParmName(std::string const & attrID) -> StringVector"""
        return _vsp.GetAttributeParmName(attrID)
    
    @client_wrap
    def GetAttributeVec3dVal(self, attrID):
        r"""GetAttributeVec3dVal(std::string const & attrID) -> Vec3dVec"""
        return _vsp.GetAttributeVec3dVal(attrID)
    
    @client_wrap
    def GetAttributeIntMatrixVal(self, attrID):
        r"""GetAttributeIntMatrixVal(std::string const & attrID) -> IntVecVec"""
        return _vsp.GetAttributeIntMatrixVal(attrID)
    
    @client_wrap
    def GetAttributeDoubleMatrixVal(self, attrID):
        r"""GetAttributeDoubleMatrixVal(std::string const & attrID) -> DoubleVecVec"""
        return _vsp.GetAttributeDoubleMatrixVal(attrID)
    
    @client_wrap
    def SetAttributeName(self, attrID, name):
        r"""SetAttributeName(std::string const & attrID, std::string const & name)"""
        return _vsp.SetAttributeName(attrID, name)
    
    @client_wrap
    def SetAttributeDoc(self, attrID, doc):
        r"""SetAttributeDoc(std::string const & attrID, std::string const & doc)"""
        return _vsp.SetAttributeDoc(attrID, doc)
    
    @client_wrap
    def SetAttributeBool(self, attrID, value):
        r"""SetAttributeBool(std::string const & attrID, bool value)"""
        return _vsp.SetAttributeBool(attrID, value)
    
    @client_wrap
    def SetAttributeInt(self, attrID, value):
        r"""SetAttributeInt(std::string const & attrID, int value)"""
        return _vsp.SetAttributeInt(attrID, value)
    
    @client_wrap
    def SetAttributeDouble(self, attrID, value):
        r"""SetAttributeDouble(std::string const & attrID, double value)"""
        return _vsp.SetAttributeDouble(attrID, value)
    
    @client_wrap
    def SetAttributeString(self, attrID, value):
        r"""SetAttributeString(std::string const & attrID, std::string const & value)"""
        return _vsp.SetAttributeString(attrID, value)
    
    @client_wrap
    def SetAttributeParmID(self, attrID, value):
        r"""SetAttributeParmID(std::string const & attrID, std::string const & value)"""
        return _vsp.SetAttributeParmID(attrID, value)
    
    @client_wrap
    def SetAttributeVec3d(self, attrID, value):
        r"""SetAttributeVec3d(std::string const & attrID, Vec3dVec value)"""
        return _vsp.SetAttributeVec3d(attrID, value)
    
    @client_wrap
    def SetAttributeIntMatrix(self, attrID, value):
        r"""SetAttributeIntMatrix(std::string const & attrID, IntVecVec value)"""
        return _vsp.SetAttributeIntMatrix(attrID, value)
    
    @client_wrap
    def SetAttributeDoubleMatrix(self, attrID, value):
        r"""SetAttributeDoubleMatrix(std::string const & attrID, DoubleVecVec value)"""
        return _vsp.SetAttributeDoubleMatrix(attrID, value)
    
    @client_wrap
    def DeleteAttribute(self, attrID):
        r"""DeleteAttribute(std::string const & attrID)"""
        return _vsp.DeleteAttribute(attrID)
    
    @client_wrap
    def AddAttributeBool(self, collID, attributeName, value):
        r"""AddAttributeBool(std::string const & collID, std::string const & attributeName, bool value) -> std::string"""
        return _vsp.AddAttributeBool(collID, attributeName, value)
    
    @client_wrap
    def AddAttributeInt(self, collID, attributeName, value):
        r"""AddAttributeInt(std::string const & collID, std::string const & attributeName, int value) -> std::string"""
        return _vsp.AddAttributeInt(collID, attributeName, value)
    
    @client_wrap
    def AddAttributeDouble(self, collID, attributeName, value):
        r"""AddAttributeDouble(std::string const & collID, std::string const & attributeName, double value) -> std::string"""
        return _vsp.AddAttributeDouble(collID, attributeName, value)
    
    @client_wrap
    def AddAttributeString(self, collID, attributeName, value):
        r"""AddAttributeString(std::string const & collID, std::string const & attributeName, std::string const & value) -> std::string"""
        return _vsp.AddAttributeString(collID, attributeName, value)
    
    @client_wrap
    def AddAttributeParm(self, collID, attributeName, parmID):
        r"""AddAttributeParm(std::string const & collID, std::string const & attributeName, std::string const & parmID) -> std::string"""
        return _vsp.AddAttributeParm(collID, attributeName, parmID)
    
    @client_wrap
    def AddAttributeVec3d(self, collID, attributeName, value):
        r"""AddAttributeVec3d(std::string const & collID, std::string const & attributeName, Vec3dVec value) -> std::string"""
        return _vsp.AddAttributeVec3d(collID, attributeName, value)
    
    @client_wrap
    def AddAttributeIntMatrix(self, collID, attributeName, value):
        r"""AddAttributeIntMatrix(std::string const & collID, std::string const & attributeName, IntVecVec value) -> std::string"""
        return _vsp.AddAttributeIntMatrix(collID, attributeName, value)
    
    @client_wrap
    def AddAttributeDoubleMatrix(self, collID, attributeName, value):
        r"""AddAttributeDoubleMatrix(std::string const & collID, std::string const & attributeName, DoubleVecVec value) -> std::string"""
        return _vsp.AddAttributeDoubleMatrix(collID, attributeName, value)
    
    @client_wrap
    def AddAttributeGroup(self, collID, attributeName):
        r"""AddAttributeGroup(std::string const & collID, std::string const & attributeName) -> std::string"""
        return _vsp.AddAttributeGroup(collID, attributeName)
    
    @client_wrap
    def CopyAttribute(self, attrID):
        r"""CopyAttribute(std::string const & attrID) -> int"""
        return _vsp.CopyAttribute(attrID)
    
    @client_wrap
    def CutAttribute(self, attrID):
        r"""CutAttribute(std::string const & attrID)"""
        return _vsp.CutAttribute(attrID)
    
    @client_wrap
    def PasteAttribute(self, coll_id):
        r"""PasteAttribute(std::string const & coll_id) -> StringVector"""
        return _vsp.PasteAttribute(coll_id)
    
    @client_wrap
    def GetAllResultsNames(self, ):
        r"""GetAllResultsNames() -> StringVector"""
        return _vsp.GetAllResultsNames()
    
    @client_wrap
    def GetAllDataNames(self, results_id):
        r"""GetAllDataNames(std::string const & results_id) -> StringVector"""
        return _vsp.GetAllDataNames(results_id)
    
    @client_wrap
    def GetNumResults(self, name):
        r"""GetNumResults(std::string const & name) -> int"""
        return _vsp.GetNumResults(name)
    
    @client_wrap
    def GetResultsName(self, results_id):
        r"""GetResultsName(std::string const & results_id) -> std::string"""
        return _vsp.GetResultsName(results_id)
    
    @client_wrap
    def GetResultsSetDoc(self, results_id):
        r"""GetResultsSetDoc(std::string const & results_id) -> std::string"""
        return _vsp.GetResultsSetDoc(results_id)
    
    @client_wrap
    def GetResultsEntryDoc(self, results_id, data_name):
        r"""GetResultsEntryDoc(std::string const & results_id, std::string const & data_name) -> std::string"""
        return _vsp.GetResultsEntryDoc(results_id, data_name)
    
    @client_wrap
    def FindResultsID(self, name, index=0):
        r"""FindResultsID(std::string const & name, int index=0) -> std::string"""
        return _vsp.FindResultsID(name, index)
    
    @client_wrap
    def FindLatestResultsID(self, name):
        r"""FindLatestResultsID(std::string const & name) -> std::string"""
        return _vsp.FindLatestResultsID(name)
    
    @client_wrap
    def GetNumData(self, results_id, data_name):
        r"""GetNumData(std::string const & results_id, std::string const & data_name) -> int"""
        return _vsp.GetNumData(results_id, data_name)
    
    @client_wrap
    def GetResultsType(self, results_id, data_name):
        r"""GetResultsType(std::string const & results_id, std::string const & data_name) -> int"""
        return _vsp.GetResultsType(results_id, data_name)
    
    @client_wrap
    def GetIntResults(self, id, name, index=0):
        r"""GetIntResults(std::string const & id, std::string const & name, int index=0) -> IntVector"""
        return _vsp.GetIntResults(id, name, index)
    
    @client_wrap
    def GetDoubleResults(self, id, name, index=0):
        r"""GetDoubleResults(std::string const & id, std::string const & name, int index=0) -> DoubleVector"""
        return _vsp.GetDoubleResults(id, name, index)
    
    @client_wrap
    def GetDoubleMatResults(self, id, name, index=0):
        r"""GetDoubleMatResults(std::string const & id, std::string const & name, int index=0) -> DoubleVecVec"""
        return _vsp.GetDoubleMatResults(id, name, index)
    
    @client_wrap
    def GetStringResults(self, id, name, index=0):
        r"""GetStringResults(std::string const & id, std::string const & name, int index=0) -> StringVector"""
        return _vsp.GetStringResults(id, name, index)
    
    @client_wrap
    def GetVec3dResults(self, id, name, index=0):
        r"""GetVec3dResults(std::string const & id, std::string const & name, int index=0) -> Vec3dVec"""
        return _vsp.GetVec3dResults(id, name, index)
    
    @client_wrap
    def CreateGeomResults(self, geom_id, name):
        r"""CreateGeomResults(std::string const & geom_id, std::string const & name) -> std::string"""
        return _vsp.CreateGeomResults(geom_id, name)
    
    @client_wrap
    def DeleteAllResults(self, ):
        r"""DeleteAllResults()"""
        return _vsp.DeleteAllResults()
    
    @client_wrap
    def DeleteResult(self, id):
        r"""DeleteResult(std::string const & id)"""
        return _vsp.DeleteResult(id)
    
    @client_wrap
    def WriteResultsCSVFile(self, id, file_name):
        r"""WriteResultsCSVFile(std::string const & id, std::string const & file_name)"""
        return _vsp.WriteResultsCSVFile(id, file_name)
    
    @client_wrap
    def PrintResults(self, results_id):
        r"""PrintResults(std::string const & results_id)"""
        return _vsp.PrintResults(results_id)
    
    @client_wrap
    def PrintResultsDocs(self, results_id):
        r"""PrintResultsDocs(std::string const & results_id)"""
        return _vsp.PrintResultsDocs(results_id)
    
    @client_wrap
    def WriteTestResults(self, ):
        r"""WriteTestResults()"""
        return _vsp.WriteTestResults()
    
    @client_wrap
    def InitGUI(self, ):
        r"""InitGUI()"""
        return _vsp.InitGUI()
    
    @client_wrap
    def StartGUI(self, ):
        r"""StartGUI()"""
        return _vsp.StartGUI()
    
    @client_wrap
    def EnableStopGUIMenuItem(self, ):
        r"""EnableStopGUIMenuItem()"""
        return _vsp.EnableStopGUIMenuItem()
    
    @client_wrap
    def DisableStopGUIMenuItem(self, ):
        r"""DisableStopGUIMenuItem()"""
        return _vsp.DisableStopGUIMenuItem()
    
    @client_wrap
    def StopGUI(self, ):
        r"""StopGUI()"""
        return _vsp.StopGUI()
    
    @client_wrap
    def PopupMsg(self, msg):
        r"""PopupMsg(std::string const & msg)"""
        return _vsp.PopupMsg(msg)
    
    @client_wrap
    def UpdateGUI(self, ):
        r"""UpdateGUI()"""
        return _vsp.UpdateGUI()
    
    @client_wrap
    def IsGUIBuild(self, ):
        r"""IsGUIBuild() -> bool"""
        return _vsp.IsGUIBuild()
    
    @client_wrap
    def Lock(self, ):
        r"""Lock()"""
        return _vsp.Lock()
    
    @client_wrap
    def Unlock(self, ):
        r"""Unlock()"""
        return _vsp.Unlock()
    
    @client_wrap
    def IsEventLoopRunning(self, ):
        r"""IsEventLoopRunning() -> bool"""
        return _vsp.IsEventLoopRunning()
    
    @client_wrap
    def ScreenGrab(self, fname, w, h, transparentBG, autocrop=False):
        r"""ScreenGrab(std::string const & fname, int w, int h, bool transparentBG, bool autocrop=False)"""
        return _vsp.ScreenGrab(fname, w, h, transparentBG, autocrop)
    
    @client_wrap
    def SetViewAxis(self, vaxis):
        r"""SetViewAxis(bool vaxis)"""
        return _vsp.SetViewAxis(vaxis)
    
    @client_wrap
    def SetShowBorders(self, brdr):
        r"""SetShowBorders(bool brdr)"""
        return _vsp.SetShowBorders(brdr)
    
    @client_wrap
    def SetGeomDrawType(self, geom_id, type):
        r"""SetGeomDrawType(std::string const & geom_id, int type)"""
        return _vsp.SetGeomDrawType(geom_id, type)
    
    @client_wrap
    def SetGeomWireColor(self, geom_id, r, g, b):
        r"""SetGeomWireColor(std::string const & geom_id, int r, int g, int b)"""
        return _vsp.SetGeomWireColor(geom_id, r, g, b)
    
    @client_wrap
    def SetGeomDisplayType(self, geom_id, type):
        r"""SetGeomDisplayType(std::string const & geom_id, int type)"""
        return _vsp.SetGeomDisplayType(geom_id, type)
    
    @client_wrap
    def GetGeomDrawType(self, geom_id):
        r"""GetGeomDrawType(std::string const & geom_id) -> int"""
        return _vsp.GetGeomDrawType(geom_id)
    
    @client_wrap
    def GetGeomDisplayType(self, geom_id):
        r"""GetGeomDisplayType(std::string const & geom_id) -> int"""
        return _vsp.GetGeomDisplayType(geom_id)
    
    @client_wrap
    def GetGeomWireColor(self, geom_id):
        r"""GetGeomWireColor(std::string const & geom_id) -> vec3d"""
        return _vsp.GetGeomWireColor(geom_id)
    
    @client_wrap
    def GetGeomMaterialName(self, geom_id):
        r"""GetGeomMaterialName(std::string const & geom_id) -> std::string"""
        return _vsp.GetGeomMaterialName(geom_id)
    
    @client_wrap
    def SetGeomMaterialName(self, geom_id, name):
        r"""SetGeomMaterialName(std::string const & geom_id, std::string const & name)"""
        return _vsp.SetGeomMaterialName(geom_id, name)
    
    @client_wrap
    def AddMaterial(self, name, ambient, diffuse, specular, emissive, alpha, shininess):
        r"""AddMaterial(std::string const & name, vec3d ambient, vec3d diffuse, vec3d specular, vec3d emissive, double const & alpha, double const & shininess)"""
        return _vsp.AddMaterial(name, ambient, diffuse, specular, emissive, alpha, shininess)
    
    @client_wrap
    def GetMaterialNames(self, ):
        r"""GetMaterialNames() -> StringVector"""
        return _vsp.GetMaterialNames()
    
    @client_wrap
    def SetBackground(self, r, g, b):
        r"""SetBackground(double r, double g, double b)"""
        return _vsp.SetBackground(r, g, b)
    
    @client_wrap
    def SetAllViews(self, view):
        r"""SetAllViews(int view)"""
        return _vsp.SetAllViews(view)
    
    @client_wrap
    def SetView(self, viewport, view):
        r"""SetView(int viewport, int view)"""
        return _vsp.SetView(viewport, view)
    
    @client_wrap
    def FitAllViews(self, ):
        r"""FitAllViews()"""
        return _vsp.FitAllViews()
    
    @client_wrap
    def ResetViews(self, ):
        r"""ResetViews()"""
        return _vsp.ResetViews()
    
    @client_wrap
    def SetWindowLayout(self, r, c):
        r"""SetWindowLayout(int r, int c)"""
        return _vsp.SetWindowLayout(r, c)
    
    @client_wrap
    def SetGUIElementDisable(self, e, state):
        r"""SetGUIElementDisable(int e, bool state)"""
        return _vsp.SetGUIElementDisable(e, state)
    
    @client_wrap
    def SetGUIScreenDisable(self, s, state):
        r"""SetGUIScreenDisable(int s, bool state)"""
        return _vsp.SetGUIScreenDisable(s, state)
    
    @client_wrap
    def SetGeomScreenDisable(self, s, state):
        r"""SetGeomScreenDisable(int s, bool state)"""
        return _vsp.SetGeomScreenDisable(s, state)
    
    @client_wrap
    def HideScreen(self, s):
        r"""HideScreen(int s)"""
        return _vsp.HideScreen(s)
    
    @client_wrap
    def ShowScreen(self, s):
        r"""ShowScreen(int s)"""
        return _vsp.ShowScreen(s)
    
    @client_wrap
    def GetGeomTypes(self, ):
        r"""GetGeomTypes() -> StringVector"""
        return _vsp.GetGeomTypes()
    
    @client_wrap
    def AddGeom(self, *args):
        r"""AddGeom(std::string const & type, std::string const & parent=std::string()) -> std::string"""
        return _vsp.AddGeom(*args)
    
    @client_wrap
    def UpdateGeom(self, geom_id):
        r"""UpdateGeom(std::string const & geom_id)"""
        return _vsp.UpdateGeom(geom_id)
    
    @client_wrap
    def DeleteGeom(self, geom_id):
        r"""DeleteGeom(std::string const & geom_id)"""
        return _vsp.DeleteGeom(geom_id)
    
    @client_wrap
    def DeleteGeomVec(self, del_vec):
        r"""DeleteGeomVec(StringVector del_vec)"""
        return _vsp.DeleteGeomVec(del_vec)
    
    @client_wrap
    def CutGeomToClipboard(self, geom_id):
        r"""CutGeomToClipboard(std::string const & geom_id)"""
        return _vsp.CutGeomToClipboard(geom_id)
    
    @client_wrap
    def CopyGeomToClipboard(self, geom_id):
        r"""CopyGeomToClipboard(std::string const & geom_id)"""
        return _vsp.CopyGeomToClipboard(geom_id)
    
    @client_wrap
    def PasteGeomClipboard(self, *args):
        r"""PasteGeomClipboard(std::string const & parent=std::string()) -> StringVector"""
        return _vsp.PasteGeomClipboard(*args)
    
    @client_wrap
    def FindGeoms(self, ):
        r"""FindGeoms() -> StringVector"""
        return _vsp.FindGeoms()
    
    @client_wrap
    def FindGeomsWithName(self, name):
        r"""FindGeomsWithName(std::string const & name) -> StringVector"""
        return _vsp.FindGeomsWithName(name)
    
    @client_wrap
    def FindGeom(self, name, index):
        r"""FindGeom(std::string const & name, int index) -> std::string"""
        return _vsp.FindGeom(name, index)
    
    @client_wrap
    def SetGeomName(self, geom_id, name):
        r"""SetGeomName(std::string const & geom_id, std::string const & name)"""
        return _vsp.SetGeomName(geom_id, name)
    
    @client_wrap
    def GetGeomName(self, geom_id):
        r"""GetGeomName(std::string const & geom_id) -> std::string"""
        return _vsp.GetGeomName(geom_id)
    
    @client_wrap
    def GetGeomParmIDs(self, geom_id):
        r"""GetGeomParmIDs(std::string const & geom_id) -> StringVector"""
        return _vsp.GetGeomParmIDs(geom_id)
    
    @client_wrap
    def GetGeomTypeName(self, geom_id):
        r"""GetGeomTypeName(std::string const & geom_id) -> std::string"""
        return _vsp.GetGeomTypeName(geom_id)
    
    @client_wrap
    def GetParm(self, geom_id, name, group):
        r"""GetParm(std::string const & geom_id, std::string const & name, std::string const & group) -> std::string"""
        return _vsp.GetParm(geom_id, name, group)
    
    @client_wrap
    def SetGeomParent(self, geom_id, parent_id):
        r"""SetGeomParent(std::string const & geom_id, std::string const & parent_id)"""
        return _vsp.SetGeomParent(geom_id, parent_id)
    
    @client_wrap
    def ReorderGeom(self, geom_id, reorder_type):
        r"""ReorderGeom(std::string const & geom_id, int reorder_type)"""
        return _vsp.ReorderGeom(geom_id, reorder_type)
    
    @client_wrap
    def AttachGeomTexture(self, geom_id, file_name):
        r"""AttachGeomTexture(std::string const & geom_id, std::string const & file_name) -> std::string"""
        return _vsp.AttachGeomTexture(geom_id, file_name)
    
    @client_wrap
    def RemoveGeomTexture(self, geom_id, texture_id):
        r"""RemoveGeomTexture(std::string const & geom_id, std::string const & texture_id)"""
        return _vsp.RemoveGeomTexture(geom_id, texture_id)
    
    @client_wrap
    def GetGeomTextureIDVec(self, geom_id):
        r"""GetGeomTextureIDVec(std::string const & geom_id) -> StringVector"""
        return _vsp.GetGeomTextureIDVec(geom_id)
    
    @client_wrap
    def GetGeomTextureFileName(self, geom_id, texture_id):
        r"""GetGeomTextureFileName(std::string const & geom_id, std::string const & texture_id) -> std::string"""
        return _vsp.GetGeomTextureFileName(geom_id, texture_id)
    
    @client_wrap
    def GetGeomParent(self, geom_id):
        r"""GetGeomParent(std::string const & geom_id) -> std::string"""
        return _vsp.GetGeomParent(geom_id)
    
    @client_wrap
    def GetGeomChildren(self, geom_id):
        r"""GetGeomChildren(std::string const & geom_id) -> StringVector"""
        return _vsp.GetGeomChildren(geom_id)
    
    @client_wrap
    def GetNumXSecSurfs(self, geom_id):
        r"""GetNumXSecSurfs(std::string const & geom_id) -> int"""
        return _vsp.GetNumXSecSurfs(geom_id)
    
    @client_wrap
    def GetNumMainSurfs(self, geom_id):
        r"""GetNumMainSurfs(std::string const & geom_id) -> int"""
        return _vsp.GetNumMainSurfs(geom_id)
    
    @client_wrap
    def GetTotalNumSurfs(self, geom_id):
        r"""GetTotalNumSurfs(std::string const & geom_id) -> int"""
        return _vsp.GetTotalNumSurfs(geom_id)
    
    @client_wrap
    def GetGeomVSPSurfType(self, geom_id, main_surf_ind=0):
        r"""GetGeomVSPSurfType(std::string const & geom_id, int main_surf_ind=0) -> int"""
        return _vsp.GetGeomVSPSurfType(geom_id, main_surf_ind)
    
    @client_wrap
    def GetGeomVSPSurfCfdType(self, geom_id, main_surf_ind=0):
        r"""GetGeomVSPSurfCfdType(std::string const & geom_id, int main_surf_ind=0) -> int"""
        return _vsp.GetGeomVSPSurfCfdType(geom_id, main_surf_ind)
    
    @client_wrap
    def GetGeomBBoxMax(self, geom_id, main_surf_ind=0, ref_frame_is_absolute=True):
        r"""GetGeomBBoxMax(std::string const & geom_id, int main_surf_ind=0, bool ref_frame_is_absolute=True) -> vec3d"""
        return _vsp.GetGeomBBoxMax(geom_id, main_surf_ind, ref_frame_is_absolute)
    
    @client_wrap
    def GetGeomBBoxMin(self, geom_id, main_surf_ind=0, ref_frame_is_absolute=True):
        r"""GetGeomBBoxMin(std::string const & geom_id, int main_surf_ind=0, bool ref_frame_is_absolute=True) -> vec3d"""
        return _vsp.GetGeomBBoxMin(geom_id, main_surf_ind, ref_frame_is_absolute)
    
    @client_wrap
    def AddSubSurf(self, geom_id, type, surfindex=0):
        r"""AddSubSurf(std::string const & geom_id, int type, int surfindex=0) -> std::string"""
        return _vsp.AddSubSurf(geom_id, type, surfindex)
    
    @client_wrap
    def GetSubSurf(self, *args):
        r"""
        GetSubSurf(std::string const & geom_id, int index) -> std::string
        GetSubSurf(std::string const & geom_id, std::string const & name) -> StringVector
        """
        return _vsp.GetSubSurf(*args)
    
    @client_wrap
    def ReorderSubSurf(self, geom_id, sub_id, reorder_type):
        r"""ReorderSubSurf(std::string const & geom_id, std::string const & sub_id, int reorder_type)"""
        return _vsp.ReorderSubSurf(geom_id, sub_id, reorder_type)
    
    @client_wrap
    def DeleteSubSurf(self, *args):
        r"""
        DeleteSubSurf(std::string const & geom_id, std::string const & sub_id)
        DeleteSubSurf(std::string const & sub_id)
        """
        return _vsp.DeleteSubSurf(*args)
    
    @client_wrap
    def SetSubSurfName(self, *args):
        r"""
        SetSubSurfName(std::string const & geom_id, std::string const & sub_id, std::string const & name)
        SetSubSurfName(std::string const & sub_id, std::string const & name)
        """
        return _vsp.SetSubSurfName(*args)
    
    @client_wrap
    def GetSubSurfName(self, *args):
        r"""
        GetSubSurfName(std::string const & geom_id, std::string const & sub_id) -> std::string
        GetSubSurfName(std::string const & sub_id) -> std::string
        """
        return _vsp.GetSubSurfName(*args)
    
    @client_wrap
    def GetSubSurfIndex(self, sub_id):
        r"""GetSubSurfIndex(std::string const & sub_id) -> int"""
        return _vsp.GetSubSurfIndex(sub_id)
    
    @client_wrap
    def GetSubSurfIDVec(self, geom_id):
        r"""GetSubSurfIDVec(std::string const & geom_id) -> StringVector"""
        return _vsp.GetSubSurfIDVec(geom_id)
    
    @client_wrap
    def GetAllSubSurfIDs(self, ):
        r"""GetAllSubSurfIDs() -> StringVector"""
        return _vsp.GetAllSubSurfIDs()
    
    @client_wrap
    def GetNumSubSurf(self, geom_id):
        r"""GetNumSubSurf(std::string const & geom_id) -> int"""
        return _vsp.GetNumSubSurf(geom_id)
    
    @client_wrap
    def GetSubSurfType(self, sub_id):
        r"""GetSubSurfType(std::string const & sub_id) -> int"""
        return _vsp.GetSubSurfType(sub_id)
    
    @client_wrap
    def GetSubSurfParmIDs(self, sub_id):
        r"""GetSubSurfParmIDs(std::string const & sub_id) -> StringVector"""
        return _vsp.GetSubSurfParmIDs(sub_id)
    
    @client_wrap
    def IntersectSubSurf(self, sub_id):
        r"""IntersectSubSurf(std::string const & sub_id)"""
        return _vsp.IntersectSubSurf(sub_id)
    
    @client_wrap
    def GetIntersectSubSurfGeomID(self, sub_id):
        r"""GetIntersectSubSurfGeomID(std::string const & sub_id) -> std::string"""
        return _vsp.GetIntersectSubSurfGeomID(sub_id)
    
    @client_wrap
    def SetIntersectSubSurfGeomID(self, sub_id, geom_id):
        r"""SetIntersectSubSurfGeomID(std::string const & sub_id, std::string const & geom_id)"""
        return _vsp.SetIntersectSubSurfGeomID(sub_id, geom_id)
    
    @client_wrap
    def AddFeaStruct(self, geom_id, init_skin=True, surfindex=0):
        r"""AddFeaStruct(std::string const & geom_id, bool init_skin=True, int surfindex=0) -> int"""
        return _vsp.AddFeaStruct(geom_id, init_skin, surfindex)
    
    @client_wrap
    def GetFeaMeshStructIndex(self, ):
        r"""GetFeaMeshStructIndex() -> int"""
        return _vsp.GetFeaMeshStructIndex()
    
    @client_wrap
    def SetFeaMeshStructIndex(self, struct_index):
        r"""SetFeaMeshStructIndex(int struct_index)"""
        return _vsp.SetFeaMeshStructIndex(struct_index)
    
    @client_wrap
    def DeleteFeaStruct(self, geom_id, fea_struct_ind):
        r"""DeleteFeaStruct(std::string const & geom_id, int fea_struct_ind)"""
        return _vsp.DeleteFeaStruct(geom_id, fea_struct_ind)
    
    @client_wrap
    def GetFeaStructID(self, geom_id, fea_struct_ind):
        r"""GetFeaStructID(std::string const & geom_id, int fea_struct_ind) -> std::string"""
        return _vsp.GetFeaStructID(geom_id, fea_struct_ind)
    
    @client_wrap
    def GetFeaStructIndex(self, struct_id):
        r"""GetFeaStructIndex(std::string const & struct_id) -> int"""
        return _vsp.GetFeaStructIndex(struct_id)
    
    @client_wrap
    def GetFeaStructParentGeomID(self, struct_id):
        r"""GetFeaStructParentGeomID(std::string const & struct_id) -> std::string"""
        return _vsp.GetFeaStructParentGeomID(struct_id)
    
    @client_wrap
    def GetFeaStructName(self, geom_id, fea_struct_ind):
        r"""GetFeaStructName(std::string const & geom_id, int fea_struct_ind) -> std::string"""
        return _vsp.GetFeaStructName(geom_id, fea_struct_ind)
    
    @client_wrap
    def SetFeaStructName(self, geom_id, fea_struct_ind, name):
        r"""SetFeaStructName(std::string const & geom_id, int fea_struct_ind, std::string const & name)"""
        return _vsp.SetFeaStructName(geom_id, fea_struct_ind, name)
    
    @client_wrap
    def GetFeaStructIDVec(self, ):
        r"""GetFeaStructIDVec() -> StringVector"""
        return _vsp.GetFeaStructIDVec()
    
    @client_wrap
    def SetFeaPartName(self, part_id, name):
        r"""SetFeaPartName(std::string const & part_id, std::string const & name)"""
        return _vsp.SetFeaPartName(part_id, name)
    
    @client_wrap
    def AddFeaPart(self, geom_id, fea_struct_ind, type):
        r"""AddFeaPart(std::string const & geom_id, int fea_struct_ind, int type) -> std::string"""
        return _vsp.AddFeaPart(geom_id, fea_struct_ind, type)
    
    @client_wrap
    def DeleteFeaPart(self, geom_id, fea_struct_ind, part_id):
        r"""DeleteFeaPart(std::string const & geom_id, int fea_struct_ind, std::string const & part_id)"""
        return _vsp.DeleteFeaPart(geom_id, fea_struct_ind, part_id)
    
    @client_wrap
    def ReorderFeaPart(self, geom_id, fea_struct_ind, part_id, reorder_type):
        r"""ReorderFeaPart(std::string const & geom_id, int fea_struct_ind, std::string const & part_id, int reorder_type)"""
        return _vsp.ReorderFeaPart(geom_id, fea_struct_ind, part_id, reorder_type)
    
    @client_wrap
    def IndividualizeFeaPart(self, geom_id, fea_struct_ind, part_id):
        r"""IndividualizeFeaPart(std::string const & geom_id, int fea_struct_ind, std::string const & part_id)"""
        return _vsp.IndividualizeFeaPart(geom_id, fea_struct_ind, part_id)
    
    @client_wrap
    def GetFeaPartID(self, fea_struct_id, fea_part_index):
        r"""GetFeaPartID(std::string const & fea_struct_id, int fea_part_index) -> std::string"""
        return _vsp.GetFeaPartID(fea_struct_id, fea_part_index)
    
    @client_wrap
    def GetFeaPartName(self, part_id):
        r"""GetFeaPartName(std::string const & part_id) -> std::string"""
        return _vsp.GetFeaPartName(part_id)
    
    @client_wrap
    def GetFeaPartType(self, part_id):
        r"""GetFeaPartType(std::string const & part_id) -> int"""
        return _vsp.GetFeaPartType(part_id)
    
    @client_wrap
    def GetFeaPartIDVec(self, fea_struct_id):
        r"""GetFeaPartIDVec(std::string const & fea_struct_id) -> StringVector"""
        return _vsp.GetFeaPartIDVec(fea_struct_id)
    
    @client_wrap
    def GetFeaSubSurfIDVec(self, fea_struct_id):
        r"""GetFeaSubSurfIDVec(std::string const & fea_struct_id) -> StringVector"""
        return _vsp.GetFeaSubSurfIDVec(fea_struct_id)
    
    @client_wrap
    def SetFeaPartPerpendicularSparID(self, part_id, perpendicular_spar_id):
        r"""SetFeaPartPerpendicularSparID(std::string const & part_id, std::string const & perpendicular_spar_id)"""
        return _vsp.SetFeaPartPerpendicularSparID(part_id, perpendicular_spar_id)
    
    @client_wrap
    def GetFeaPartPerpendicularSparID(self, part_id):
        r"""GetFeaPartPerpendicularSparID(std::string const & part_id) -> std::string"""
        return _vsp.GetFeaPartPerpendicularSparID(part_id)
    
    @client_wrap
    def SetFeaSubSurfName(self, subsurf_id, name):
        r"""SetFeaSubSurfName(std::string const & subsurf_id, std::string const & name)"""
        return _vsp.SetFeaSubSurfName(subsurf_id, name)
    
    @client_wrap
    def GetFeaSubSurfName(self, subsurf_id):
        r"""GetFeaSubSurfName(std::string const & subsurf_id) -> std::string"""
        return _vsp.GetFeaSubSurfName(subsurf_id)
    
    @client_wrap
    def AddFeaSubSurf(self, geom_id, fea_struct_ind, type):
        r"""AddFeaSubSurf(std::string const & geom_id, int fea_struct_ind, int type) -> std::string"""
        return _vsp.AddFeaSubSurf(geom_id, fea_struct_ind, type)
    
    @client_wrap
    def DeleteFeaSubSurf(self, geom_id, fea_struct_ind, ss_id):
        r"""DeleteFeaSubSurf(std::string const & geom_id, int fea_struct_ind, std::string const & ss_id)"""
        return _vsp.DeleteFeaSubSurf(geom_id, fea_struct_ind, ss_id)
    
    @client_wrap
    def GetFeaSubSurfIndex(self, ss_id):
        r"""GetFeaSubSurfIndex(std::string const & ss_id) -> int"""
        return _vsp.GetFeaSubSurfIndex(ss_id)
    
    @client_wrap
    def GetFeaPolySparNumPt(self, pspar_id):
        r"""GetFeaPolySparNumPt(std::string const & pspar_id) -> int"""
        return _vsp.GetFeaPolySparNumPt(pspar_id)
    
    @client_wrap
    def AddFeaPolySparPt(self, pspar_id):
        r"""AddFeaPolySparPt(std::string const & pspar_id) -> std::string"""
        return _vsp.AddFeaPolySparPt(pspar_id)
    
    @client_wrap
    def InsertFeaPolySparPt(self, pspar_id, index):
        r"""InsertFeaPolySparPt(std::string const & pspar_id, int index) -> std::string"""
        return _vsp.InsertFeaPolySparPt(pspar_id, index)
    
    @client_wrap
    def DelFeaPolySparPt(self, pspar_id, index):
        r"""DelFeaPolySparPt(std::string const & pspar_id, int index)"""
        return _vsp.DelFeaPolySparPt(pspar_id, index)
    
    @client_wrap
    def DelAllFeaPolySparPt(self, pspar_id):
        r"""DelAllFeaPolySparPt(std::string const & pspar_id)"""
        return _vsp.DelAllFeaPolySparPt(pspar_id)
    
    @client_wrap
    def MoveFeaPolySparPt(self, pspar_id, index, reorder_type):
        r"""MoveFeaPolySparPt(std::string const & pspar_id, int index, int reorder_type) -> int"""
        return _vsp.MoveFeaPolySparPt(pspar_id, index, reorder_type)
    
    @client_wrap
    def SetFeaPolySparPtName(self, pspar_id, index, name):
        r"""SetFeaPolySparPtName(std::string const & pspar_id, int index, std::string const & name)"""
        return _vsp.SetFeaPolySparPtName(pspar_id, index, name)
    
    @client_wrap
    def GetFeaPolySparPtName(self, pspar_id, index):
        r"""GetFeaPolySparPtName(std::string const & pspar_id, int index) -> std::string"""
        return _vsp.GetFeaPolySparPtName(pspar_id, index)
    
    @client_wrap
    def GetFeaPolySparPtID(self, pspar_id, index):
        r"""GetFeaPolySparPtID(std::string const & pspar_id, int index) -> std::string"""
        return _vsp.GetFeaPolySparPtID(pspar_id, index)
    
    @client_wrap
    def GetAllFeaPolySparPtIDVec(self, pspar_id):
        r"""GetAllFeaPolySparPtIDVec(std::string const & pspar_id) -> StringVector"""
        return _vsp.GetAllFeaPolySparPtIDVec(pspar_id)
    
    @client_wrap
    def AddFeaTrimPart(self, trim_id, part_id):
        r"""AddFeaTrimPart(std::string const & trim_id, std::string const & part_id)"""
        return _vsp.AddFeaTrimPart(trim_id, part_id)
    
    @client_wrap
    def DeleteFeaTrimPart(self, trim_id, index):
        r"""DeleteFeaTrimPart(std::string const & trim_id, int index)"""
        return _vsp.DeleteFeaTrimPart(trim_id, index)
    
    @client_wrap
    def GetFeaTrimPartIDVec(self, trim_id):
        r"""GetFeaTrimPartIDVec(std::string const & trim_id) -> StringVector"""
        return _vsp.GetFeaTrimPartIDVec(trim_id)
    
    @client_wrap
    def AddFeaAssembly(self, ):
        r"""AddFeaAssembly() -> std::string"""
        return _vsp.AddFeaAssembly()
    
    @client_wrap
    def DeleteFeaAssembly(self, assembly_id):
        r"""DeleteFeaAssembly(std::string const & assembly_id)"""
        return _vsp.DeleteFeaAssembly(assembly_id)
    
    @client_wrap
    def NumFeaAssemblies(self, ):
        r"""NumFeaAssemblies() -> int"""
        return _vsp.NumFeaAssemblies()
    
    @client_wrap
    def GetFeaAssemblyIDVec(self, ):
        r"""GetFeaAssemblyIDVec() -> StringVector"""
        return _vsp.GetFeaAssemblyIDVec()
    
    @client_wrap
    def GetFeaAssemblyName(self, assembly_id):
        r"""GetFeaAssemblyName(std::string const & assembly_id) -> std::string"""
        return _vsp.GetFeaAssemblyName(assembly_id)
    
    @client_wrap
    def SetFeaAssemblyName(self, assembly_id, name):
        r"""SetFeaAssemblyName(std::string const & assembly_id, std::string const & name)"""
        return _vsp.SetFeaAssemblyName(assembly_id, name)
    
    @client_wrap
    def AddFeaStructureToAssembly(self, assembly_id, struct_id):
        r"""AddFeaStructureToAssembly(std::string const & assembly_id, std::string const & struct_id)"""
        return _vsp.AddFeaStructureToAssembly(assembly_id, struct_id)
    
    @client_wrap
    def DeleteFeaStructureFromAssembly(self, assembly_id, struct_id):
        r"""DeleteFeaStructureFromAssembly(std::string const & assembly_id, std::string const & struct_id)"""
        return _vsp.DeleteFeaStructureFromAssembly(assembly_id, struct_id)
    
    @client_wrap
    def GetFeaAssemblyStructureIDVec(self, assembly_id):
        r"""GetFeaAssemblyStructureIDVec(std::string const & assembly_id) -> StringVector"""
        return _vsp.GetFeaAssemblyStructureIDVec(assembly_id)
    
    @client_wrap
    def AddFeaAssemblyConnection(self, assembly_id, start_fix_pt_id, start_struct_id, start_index, end_fix_pt_id, end_struct_id, end_index):
        r"""AddFeaAssemblyConnection(std::string const & assembly_id, std::string const & start_fix_pt_id, std::string const & start_struct_id, int start_index, std::string const & end_fix_pt_id, std::string const & end_struct_id, int end_index)"""
        return _vsp.AddFeaAssemblyConnection(assembly_id, start_fix_pt_id, start_struct_id, start_index, end_fix_pt_id, end_struct_id, end_index)
    
    @client_wrap
    def DeleteFeaAssemblyConnection(self, assembly_id, connection_index):
        r"""DeleteFeaAssemblyConnection(std::string const & assembly_id, int connection_index)"""
        return _vsp.DeleteFeaAssemblyConnection(assembly_id, connection_index)
    
    @client_wrap
    def NumFeaAssemblyConnections(self, assembly_id):
        r"""NumFeaAssemblyConnections(std::string const & assembly_id) -> int"""
        return _vsp.NumFeaAssemblyConnections(assembly_id)
    
    @client_wrap
    def GetFeaAssemblyFileName(self, assembly_id, file_type):
        r"""GetFeaAssemblyFileName(std::string const & assembly_id, int file_type) -> std::string"""
        return _vsp.GetFeaAssemblyFileName(assembly_id, file_type)
    
    @client_wrap
    def SetFeaAssemblyFileName(self, assembly_id, file_type, file_name):
        r"""SetFeaAssemblyFileName(std::string const & assembly_id, int file_type, std::string const & file_name)"""
        return _vsp.SetFeaAssemblyFileName(assembly_id, file_type, file_name)
    
    @client_wrap
    def ComputeFeaAssemblyMesh(self, assembly_id):
        r"""ComputeFeaAssemblyMesh(std::string const & assembly_id)"""
        return _vsp.ComputeFeaAssemblyMesh(assembly_id)
    
    @client_wrap
    def NumFeaStructures(self, ):
        r"""NumFeaStructures() -> int"""
        return _vsp.NumFeaStructures()
    
    @client_wrap
    def NumFeaParts(self, fea_struct_id):
        r"""NumFeaParts(std::string const & fea_struct_id) -> int"""
        return _vsp.NumFeaParts(fea_struct_id)
    
    @client_wrap
    def NumFeaSubSurfs(self, fea_struct_id):
        r"""NumFeaSubSurfs(std::string const & fea_struct_id) -> int"""
        return _vsp.NumFeaSubSurfs(fea_struct_id)
    
    @client_wrap
    def AddFeaBC(self, fea_struct_id, type=-1):
        r"""AddFeaBC(std::string const & fea_struct_id, int type=-1) -> std::string"""
        return _vsp.AddFeaBC(fea_struct_id, type)
    
    @client_wrap
    def DelFeaBC(self, fea_struct_id, bc_id):
        r"""DelFeaBC(std::string const & fea_struct_id, std::string const & bc_id)"""
        return _vsp.DelFeaBC(fea_struct_id, bc_id)
    
    @client_wrap
    def GetFeaBCIDVec(self, fea_struct_id):
        r"""GetFeaBCIDVec(std::string const & fea_struct_id) -> StringVector"""
        return _vsp.GetFeaBCIDVec(fea_struct_id)
    
    @client_wrap
    def NumFeaBCs(self, fea_struct_id):
        r"""NumFeaBCs(std::string const & fea_struct_id) -> int"""
        return _vsp.NumFeaBCs(fea_struct_id)
    
    @client_wrap
    def AddFeaLayer(self, material_id):
        r"""AddFeaLayer(std::string const & material_id) -> std::string"""
        return _vsp.AddFeaLayer(material_id)
    
    @client_wrap
    def DeleteFeaLayer(self, material_id, layer_id):
        r"""DeleteFeaLayer(std::string const & material_id, std::string const & layer_id)"""
        return _vsp.DeleteFeaLayer(material_id, layer_id)
    
    @client_wrap
    def ReorderFeaLayer(self, material_id, layer_id, reorder_type):
        r"""ReorderFeaLayer(std::string const & material_id, std::string const & layer_id, int reorder_type)"""
        return _vsp.ReorderFeaLayer(material_id, layer_id, reorder_type)
    
    @client_wrap
    def NumFeaLayers(self, material_id):
        r"""NumFeaLayers(std::string const & material_id) -> int"""
        return _vsp.NumFeaLayers(material_id)
    
    @client_wrap
    def GetFeaLayerIDVec(self, material_id):
        r"""GetFeaLayerIDVec(std::string const & material_id) -> StringVector"""
        return _vsp.GetFeaLayerIDVec(material_id)
    
    @client_wrap
    def GetFeaMaterialIDVec(self, ):
        r"""GetFeaMaterialIDVec() -> StringVector"""
        return _vsp.GetFeaMaterialIDVec()
    
    @client_wrap
    def DeleteFeaMaterial(self, id):
        r"""DeleteFeaMaterial(std::string const & id)"""
        return _vsp.DeleteFeaMaterial(id)
    
    @client_wrap
    def AddFeaMaterial(self, ):
        r"""AddFeaMaterial() -> std::string"""
        return _vsp.AddFeaMaterial()
    
    @client_wrap
    def GetFeaPropertyIDVec(self, ):
        r"""GetFeaPropertyIDVec() -> StringVector"""
        return _vsp.GetFeaPropertyIDVec()
    
    @client_wrap
    def DeleteFeaProperty(self, id):
        r"""DeleteFeaProperty(std::string const & id)"""
        return _vsp.DeleteFeaProperty(id)
    
    @client_wrap
    def AddFeaProperty(self, property_type=0):
        r"""AddFeaProperty(int property_type=0) -> std::string"""
        return _vsp.AddFeaProperty(property_type)
    
    @client_wrap
    def GetFeaMeshVal(self, geom_id, fea_struct_ind, type):
        r"""GetFeaMeshVal(std::string const & geom_id, int fea_struct_ind, int type) -> double"""
        return _vsp.GetFeaMeshVal(geom_id, fea_struct_ind, type)
    
    @client_wrap
    def SetFeaMeshVal(self, geom_id, fea_struct_ind, type, val):
        r"""SetFeaMeshVal(std::string const & geom_id, int fea_struct_ind, int type, double val)"""
        return _vsp.SetFeaMeshVal(geom_id, fea_struct_ind, type, val)
    
    @client_wrap
    def GetFeaMeshFileName(self, geom_id, fea_struct_ind, file_type):
        r"""GetFeaMeshFileName(std::string const & geom_id, int fea_struct_ind, int file_type) -> std::string"""
        return _vsp.GetFeaMeshFileName(geom_id, fea_struct_ind, file_type)
    
    @client_wrap
    def SetFeaMeshFileName(self, geom_id, fea_struct_ind, file_type, file_name):
        r"""SetFeaMeshFileName(std::string const & geom_id, int fea_struct_ind, int file_type, std::string const & file_name)"""
        return _vsp.SetFeaMeshFileName(geom_id, fea_struct_ind, file_type, file_name)
    
    @client_wrap
    def ComputeFeaMesh(self, *args):
        r"""
        ComputeFeaMesh(std::string const & geom_id, int fea_struct_ind, int file_type)
        ComputeFeaMesh(std::string const & struct_id, int file_type)
        """
        return _vsp.ComputeFeaMesh(*args)
    
    @client_wrap
    def SetXSecAlias(self, id, alias):
        r"""SetXSecAlias(std::string const & id, std::string const & alias)"""
        return _vsp.SetXSecAlias(id, alias)
    
    @client_wrap
    def GetXSecAlias(self, id):
        r"""GetXSecAlias(std::string const & id) -> std::string"""
        return _vsp.GetXSecAlias(id)
    
    @client_wrap
    def SetXSecCurveAlias(self, id, alias):
        r"""SetXSecCurveAlias(std::string const & id, std::string const & alias)"""
        return _vsp.SetXSecCurveAlias(id, alias)
    
    @client_wrap
    def GetXSecCurveAlias(self, id):
        r"""GetXSecCurveAlias(std::string const & id) -> std::string"""
        return _vsp.GetXSecCurveAlias(id)
    
    @client_wrap
    def CutXSec(self, geom_id, index):
        r"""CutXSec(std::string const & geom_id, int index)"""
        return _vsp.CutXSec(geom_id, index)
    
    @client_wrap
    def CopyXSec(self, geom_id, index):
        r"""CopyXSec(std::string const & geom_id, int index)"""
        return _vsp.CopyXSec(geom_id, index)
    
    @client_wrap
    def PasteXSec(self, geom_id, index):
        r"""PasteXSec(std::string const & geom_id, int index)"""
        return _vsp.PasteXSec(geom_id, index)
    
    @client_wrap
    def InsertXSec(self, geom_id, index, type):
        r"""InsertXSec(std::string const & geom_id, int index, int type)"""
        return _vsp.InsertXSec(geom_id, index, type)
    
    @client_wrap
    def SplitWingXSec(self, wing_id, section_index):
        r"""SplitWingXSec(std::string const & wing_id, int section_index)"""
        return _vsp.SplitWingXSec(wing_id, section_index)
    
    @client_wrap
    def GetDriverGroup(self, geom_id, section_index):
        r"""GetDriverGroup(std::string const & geom_id, int section_index) -> IntVector"""
        return _vsp.GetDriverGroup(geom_id, section_index)
    
    @client_wrap
    def SetDriverGroup(self, geom_id, section_index, driver_0, driver_1=-1, driver_2=-1):
        r"""SetDriverGroup(std::string const & geom_id, int section_index, int driver_0, int driver_1=-1, int driver_2=-1)"""
        return _vsp.SetDriverGroup(geom_id, section_index, driver_0, driver_1, driver_2)
    
    @client_wrap
    def GetXSecSurf(self, geom_id, index):
        r"""GetXSecSurf(std::string const & geom_id, int index) -> std::string"""
        return _vsp.GetXSecSurf(geom_id, index)
    
    @client_wrap
    def GetNumXSec(self, xsec_surf_id):
        r"""GetNumXSec(std::string const & xsec_surf_id) -> int"""
        return _vsp.GetNumXSec(xsec_surf_id)
    
    @client_wrap
    def GetXSec(self, xsec_surf_id, xsec_index):
        r"""GetXSec(std::string const & xsec_surf_id, int xsec_index) -> std::string"""
        return _vsp.GetXSec(xsec_surf_id, xsec_index)
    
    @client_wrap
    def ChangeXSecShape(self, xsec_surf_id, xsec_index, type):
        r"""ChangeXSecShape(std::string const & xsec_surf_id, int xsec_index, int type)"""
        return _vsp.ChangeXSecShape(xsec_surf_id, xsec_index, type)
    
    @client_wrap
    def FitCSTAirfoil(self, xsec_surf_id, xsec_index, deg):
        r"""FitCSTAirfoil(std::string const & xsec_surf_id, int xsec_index, int deg)"""
        return _vsp.FitCSTAirfoil(xsec_surf_id, xsec_index, deg)
    
    @client_wrap
    def SetXSecSurfGlobalXForm(self, xsec_surf_id, mat):
        r"""SetXSecSurfGlobalXForm(std::string const & xsec_surf_id, Matrix4d mat)"""
        return _vsp.SetXSecSurfGlobalXForm(xsec_surf_id, mat)
    
    @client_wrap
    def GetXSecSurfGlobalXForm(self, xsec_surf_id):
        r"""GetXSecSurfGlobalXForm(std::string const & xsec_surf_id) -> Matrix4d"""
        return _vsp.GetXSecSurfGlobalXForm(xsec_surf_id)
    
    @client_wrap
    def GetXSecShape(self, xsec_id):
        r"""GetXSecShape(std::string const & xsec_id) -> int"""
        return _vsp.GetXSecShape(xsec_id)
    
    @client_wrap
    def GetXSecWidth(self, xsec_id):
        r"""GetXSecWidth(std::string const & xsec_id) -> double"""
        return _vsp.GetXSecWidth(xsec_id)
    
    @client_wrap
    def GetXSecHeight(self, xsec_id):
        r"""GetXSecHeight(std::string const & xsec_id) -> double"""
        return _vsp.GetXSecHeight(xsec_id)
    
    @client_wrap
    def SetXSecWidthHeight(self, xsec_id, w, h):
        r"""SetXSecWidthHeight(std::string const & xsec_id, double w, double h)"""
        return _vsp.SetXSecWidthHeight(xsec_id, w, h)
    
    @client_wrap
    def SetXSecWidth(self, xsec_id, w):
        r"""SetXSecWidth(std::string const & xsec_id, double w)"""
        return _vsp.SetXSecWidth(xsec_id, w)
    
    @client_wrap
    def SetXSecHeight(self, xsec_id, h):
        r"""SetXSecHeight(std::string const & xsec_id, double h)"""
        return _vsp.SetXSecHeight(xsec_id, h)
    
    @client_wrap
    def GetXSecParmIDs(self, xsec_id):
        r"""GetXSecParmIDs(std::string const & xsec_id) -> StringVector"""
        return _vsp.GetXSecParmIDs(xsec_id)
    
    @client_wrap
    def GetXSecParm(self, xsec_id, name):
        r"""GetXSecParm(std::string const & xsec_id, std::string const & name) -> std::string"""
        return _vsp.GetXSecParm(xsec_id, name)
    
    @client_wrap
    def ReadFileXSec(self, xsec_id, file_name):
        r"""ReadFileXSec(std::string const & xsec_id, std::string const & file_name) -> Vec3dVec"""
        return _vsp.ReadFileXSec(xsec_id, file_name)
    
    @client_wrap
    def GetXSecPnts(self, xsec_id):
        r"""GetXSecPnts(std::string const & xsec_id) -> Vec3dVec"""
        return _vsp.GetXSecPnts(xsec_id)
    
    @client_wrap
    def CopyXSecCurve(self, geom_id, index):
        r"""CopyXSecCurve(std::string const & geom_id, int index)"""
        return _vsp.CopyXSecCurve(geom_id, index)
    
    @client_wrap
    def PasteXSecCurve(self, geom_id, index):
        r"""PasteXSecCurve(std::string const & geom_id, int index)"""
        return _vsp.PasteXSecCurve(geom_id, index)
    
    @client_wrap
    def SetXSecPnts(self, xsec_id, pnt_vec):
        r"""SetXSecPnts(std::string const & xsec_id, Vec3dVec pnt_vec)"""
        return _vsp.SetXSecPnts(xsec_id, pnt_vec)
    
    @client_wrap
    def ComputeXSecPnt(self, xsec_id, fract):
        r"""ComputeXSecPnt(std::string const & xsec_id, double fract) -> vec3d"""
        return _vsp.ComputeXSecPnt(xsec_id, fract)
    
    @client_wrap
    def ComputeXSecTan(self, xsec_id, fract):
        r"""ComputeXSecTan(std::string const & xsec_id, double fract) -> vec3d"""
        return _vsp.ComputeXSecTan(xsec_id, fract)
    
    @client_wrap
    def ResetXSecSkinParms(self, xsec_id):
        r"""ResetXSecSkinParms(std::string const & xsec_id)"""
        return _vsp.ResetXSecSkinParms(xsec_id)
    
    @client_wrap
    def GetXSecContinuity(self, xsec_id):
        r"""GetXSecContinuity(std::string const & xsec_id) -> int"""
        return _vsp.GetXSecContinuity(xsec_id)
    
    @client_wrap
    def SetXSecContinuity(self, xsec_id, cx):
        r"""SetXSecContinuity(std::string const & xsec_id, int cx)"""
        return _vsp.SetXSecContinuity(xsec_id, cx)
    
    @client_wrap
    def GetXSecTanAngles(self, xsec_id, side):
        r"""GetXSecTanAngles(std::string const & xsec_id, int side) -> DoubleVector"""
        return _vsp.GetXSecTanAngles(xsec_id, side)
    
    @client_wrap
    def SetXSecTanAngles(self, xsec_id, side, top, right, bottom, left):
        r"""SetXSecTanAngles(std::string const & xsec_id, int side, double top, double right, double bottom, double left)"""
        return _vsp.SetXSecTanAngles(xsec_id, side, top, right, bottom, left)
    
    @client_wrap
    def GetXSecTanSlews(self, xsec_id, side):
        r"""GetXSecTanSlews(std::string const & xsec_id, int side) -> DoubleVector"""
        return _vsp.GetXSecTanSlews(xsec_id, side)
    
    @client_wrap
    def SetXSecTanSlews(self, xsec_id, side, top, right, bottom, left):
        r"""SetXSecTanSlews(std::string const & xsec_id, int side, double top, double right, double bottom, double left)"""
        return _vsp.SetXSecTanSlews(xsec_id, side, top, right, bottom, left)
    
    @client_wrap
    def GetXSecTanStrengths(self, xsec_id, side):
        r"""GetXSecTanStrengths(std::string const & xsec_id, int side) -> DoubleVector"""
        return _vsp.GetXSecTanStrengths(xsec_id, side)
    
    @client_wrap
    def SetXSecTanStrengths(self, xsec_id, side, top, right, bottom, left):
        r"""SetXSecTanStrengths(std::string const & xsec_id, int side, double top, double right, double bottom, double left)"""
        return _vsp.SetXSecTanStrengths(xsec_id, side, top, right, bottom, left)
    
    @client_wrap
    def GetXSecCurvatures(self, xsec_id, side):
        r"""GetXSecCurvatures(std::string const & xsec_id, int side) -> DoubleVector"""
        return _vsp.GetXSecCurvatures(xsec_id, side)
    
    @client_wrap
    def SetXSecCurvatures(self, xsec_id, side, top, right, bottom, left):
        r"""SetXSecCurvatures(std::string const & xsec_id, int side, double top, double right, double bottom, double left)"""
        return _vsp.SetXSecCurvatures(xsec_id, side, top, right, bottom, left)
    
    @client_wrap
    def ReadFileAirfoil(self, xsec_id, file_name):
        r"""ReadFileAirfoil(std::string const & xsec_id, std::string const & file_name)"""
        return _vsp.ReadFileAirfoil(xsec_id, file_name)
    
    @client_wrap
    def SetAirfoilUpperPnts(self, xsec_id, up_pnt_vec):
        r"""SetAirfoilUpperPnts(std::string const & xsec_id, Vec3dVec up_pnt_vec)"""
        return _vsp.SetAirfoilUpperPnts(xsec_id, up_pnt_vec)
    
    @client_wrap
    def SetAirfoilLowerPnts(self, xsec_id, low_pnt_vec):
        r"""SetAirfoilLowerPnts(std::string const & xsec_id, Vec3dVec low_pnt_vec)"""
        return _vsp.SetAirfoilLowerPnts(xsec_id, low_pnt_vec)
    
    @client_wrap
    def SetAirfoilPnts(self, xsec_id, up_pnt_vec, low_pnt_vec):
        r"""SetAirfoilPnts(std::string const & xsec_id, Vec3dVec up_pnt_vec, Vec3dVec low_pnt_vec)"""
        return _vsp.SetAirfoilPnts(xsec_id, up_pnt_vec, low_pnt_vec)
    
    @client_wrap
    def GetHersheyBarLiftDist(self, npts, alpha, Vinf, span, full_span_flag=False):
        r"""GetHersheyBarLiftDist(int const & npts, double const & alpha, double const & Vinf, double const & span, bool full_span_flag=False) -> Vec3dVec"""
        return _vsp.GetHersheyBarLiftDist(npts, alpha, Vinf, span, full_span_flag)
    
    @client_wrap
    def GetHersheyBarDragDist(self, npts, alpha, Vinf, span, full_span_flag=False):
        r"""GetHersheyBarDragDist(int const & npts, double const & alpha, double const & Vinf, double const & span, bool full_span_flag=False) -> Vec3dVec"""
        return _vsp.GetHersheyBarDragDist(npts, alpha, Vinf, span, full_span_flag)
    
    @client_wrap
    def GetVKTAirfoilPnts(self, npts, alpha, epsilon, kappa, tau):
        r"""GetVKTAirfoilPnts(int const & npts, double const & alpha, double const & epsilon, double const & kappa, double const & tau) -> Vec3dVec"""
        return _vsp.GetVKTAirfoilPnts(npts, alpha, epsilon, kappa, tau)
    
    @client_wrap
    def GetVKTAirfoilCpDist(self, alpha, epsilon, kappa, tau, xyz_data):
        r"""GetVKTAirfoilCpDist(double const & alpha, double const & epsilon, double const & kappa, double const & tau, Vec3dVec xyz_data) -> DoubleVector"""
        return _vsp.GetVKTAirfoilCpDist(alpha, epsilon, kappa, tau, xyz_data)
    
    @client_wrap
    def GetEllipsoidSurfPnts(self, center, abc_rad, u_npts=20, w_npts=20):
        r"""GetEllipsoidSurfPnts(vec3d center, vec3d abc_rad, int u_npts=20, int w_npts=20) -> Vec3dVec"""
        return _vsp.GetEllipsoidSurfPnts(center, abc_rad, u_npts, w_npts)
    
    @client_wrap
    def GetFeatureLinePnts(self, geom_id):
        r"""GetFeatureLinePnts(std::string const & geom_id) -> Vec3dVec"""
        return _vsp.GetFeatureLinePnts(geom_id)
    
    @client_wrap
    def GetEllipsoidCpDist(self, surf_pnt_vec, abc_rad, V_inf):
        r"""GetEllipsoidCpDist(Vec3dVec surf_pnt_vec, vec3d abc_rad, vec3d V_inf) -> DoubleVector"""
        return _vsp.GetEllipsoidCpDist(surf_pnt_vec, abc_rad, V_inf)
    
    @client_wrap
    def IntegrateEllipsoidFlow(self, abc_rad, abc_index):
        r"""IntegrateEllipsoidFlow(vec3d abc_rad, int const & abc_index) -> double"""
        return _vsp.IntegrateEllipsoidFlow(abc_rad, abc_index)
    
    @client_wrap
    def GetAirfoilUpperPnts(self, xsec_id):
        r"""GetAirfoilUpperPnts(std::string const & xsec_id) -> Vec3dVec"""
        return _vsp.GetAirfoilUpperPnts(xsec_id)
    
    @client_wrap
    def GetAirfoilLowerPnts(self, xsec_id):
        r"""GetAirfoilLowerPnts(std::string const & xsec_id) -> Vec3dVec"""
        return _vsp.GetAirfoilLowerPnts(xsec_id)
    
    @client_wrap
    def GetUpperCSTCoefs(self, xsec_id):
        r"""GetUpperCSTCoefs(std::string const & xsec_id) -> DoubleVector"""
        return _vsp.GetUpperCSTCoefs(xsec_id)
    
    @client_wrap
    def GetLowerCSTCoefs(self, xsec_id):
        r"""GetLowerCSTCoefs(std::string const & xsec_id) -> DoubleVector"""
        return _vsp.GetLowerCSTCoefs(xsec_id)
    
    @client_wrap
    def GetUpperCSTDegree(self, xsec_id):
        r"""GetUpperCSTDegree(std::string const & xsec_id) -> int"""
        return _vsp.GetUpperCSTDegree(xsec_id)
    
    @client_wrap
    def GetLowerCSTDegree(self, xsec_id):
        r"""GetLowerCSTDegree(std::string const & xsec_id) -> int"""
        return _vsp.GetLowerCSTDegree(xsec_id)
    
    @client_wrap
    def SetUpperCST(self, xsec_id, deg, coefs):
        r"""SetUpperCST(std::string const & xsec_id, int deg, DoubleVector coefs)"""
        return _vsp.SetUpperCST(xsec_id, deg, coefs)
    
    @client_wrap
    def SetLowerCST(self, xsec_id, deg, coefs):
        r"""SetLowerCST(std::string const & xsec_id, int deg, DoubleVector coefs)"""
        return _vsp.SetLowerCST(xsec_id, deg, coefs)
    
    @client_wrap
    def PromoteCSTUpper(self, xsec_id):
        r"""PromoteCSTUpper(std::string const & xsec_id)"""
        return _vsp.PromoteCSTUpper(xsec_id)
    
    @client_wrap
    def PromoteCSTLower(self, xsec_id):
        r"""PromoteCSTLower(std::string const & xsec_id)"""
        return _vsp.PromoteCSTLower(xsec_id)
    
    @client_wrap
    def DemoteCSTUpper(self, xsec_id):
        r"""DemoteCSTUpper(std::string const & xsec_id)"""
        return _vsp.DemoteCSTUpper(xsec_id)
    
    @client_wrap
    def DemoteCSTLower(self, xsec_id):
        r"""DemoteCSTLower(std::string const & xsec_id)"""
        return _vsp.DemoteCSTLower(xsec_id)
    
    @client_wrap
    def FitAfCST(self, xsec_surf_id, xsec_index, deg):
        r"""FitAfCST(std::string const & xsec_surf_id, int xsec_index, int deg)"""
        return _vsp.FitAfCST(xsec_surf_id, xsec_index, deg)
    
    @client_wrap
    def AddBackground3D(self, ):
        r"""AddBackground3D() -> std::string"""
        return _vsp.AddBackground3D()
    
    @client_wrap
    def GetNumBackground3Ds(self, ):
        r"""GetNumBackground3Ds() -> int"""
        return _vsp.GetNumBackground3Ds()
    
    @client_wrap
    def GetAllBackground3Ds(self, ):
        r"""GetAllBackground3Ds() -> StringVector"""
        return _vsp.GetAllBackground3Ds()
    
    @client_wrap
    def ShowAllBackground3Ds(self, ):
        r"""ShowAllBackground3Ds()"""
        return _vsp.ShowAllBackground3Ds()
    
    @client_wrap
    def HideAllBackground3Ds(self, ):
        r"""HideAllBackground3Ds()"""
        return _vsp.HideAllBackground3Ds()
    
    @client_wrap
    def DelAllBackground3Ds(self, ):
        r"""DelAllBackground3Ds()"""
        return _vsp.DelAllBackground3Ds()
    
    @client_wrap
    def DelBackground3D(self, id):
        r"""DelBackground3D(std::string const & id)"""
        return _vsp.DelBackground3D(id)
    
    @client_wrap
    def GetAllBackground3DRelativePaths(self, ):
        r"""GetAllBackground3DRelativePaths() -> StringVector"""
        return _vsp.GetAllBackground3DRelativePaths()
    
    @client_wrap
    def GetAllBackground3DAbsolutePaths(self, ):
        r"""GetAllBackground3DAbsolutePaths() -> StringVector"""
        return _vsp.GetAllBackground3DAbsolutePaths()
    
    @client_wrap
    def GetBackground3DRelativePath(self, id):
        r"""GetBackground3DRelativePath(std::string const & id) -> std::string"""
        return _vsp.GetBackground3DRelativePath(id)
    
    @client_wrap
    def GetBackground3DAbsolutePath(self, id):
        r"""GetBackground3DAbsolutePath(std::string const & id) -> std::string"""
        return _vsp.GetBackground3DAbsolutePath(id)
    
    @client_wrap
    def SetBackground3DRelativePath(self, id, fname):
        r"""SetBackground3DRelativePath(std::string const & id, std::string const & fname)"""
        return _vsp.SetBackground3DRelativePath(id, fname)
    
    @client_wrap
    def SetBackground3DAbsolutePath(self, id, fname):
        r"""SetBackground3DAbsolutePath(std::string const & id, std::string const & fname)"""
        return _vsp.SetBackground3DAbsolutePath(id, fname)
    
    @client_wrap
    def CreateAndAddBogie(self, gear_id):
        r"""CreateAndAddBogie(std::string const & gear_id) -> std::string"""
        return _vsp.CreateAndAddBogie(gear_id)
    
    @client_wrap
    def GetNumBogies(self, gear_id):
        r"""GetNumBogies(std::string const & gear_id) -> int"""
        return _vsp.GetNumBogies(gear_id)
    
    @client_wrap
    def GetAllBogies(self, gear_id):
        r"""GetAllBogies(std::string const & gear_id) -> StringVector"""
        return _vsp.GetAllBogies(gear_id)
    
    @client_wrap
    def DelBogie(self, gear_id, bogie_id):
        r"""DelBogie(std::string const & gear_id, std::string const & bogie_id)"""
        return _vsp.DelBogie(gear_id, bogie_id)
    
    @client_wrap
    def DelAllBogies(self, gear_id):
        r"""DelAllBogies(std::string const & gear_id)"""
        return _vsp.DelAllBogies(gear_id)
    
    @client_wrap
    def SetAuxiliaryGeomContactPtID(self, geom_id, index, bogie_id):
        r"""SetAuxiliaryGeomContactPtID(std::string const & geom_id, int index, std::string const & bogie_id)"""
        return _vsp.SetAuxiliaryGeomContactPtID(geom_id, index, bogie_id)
    
    @client_wrap
    def GetAuxiliaryGeomContactPtID(self, geom_id, index):
        r"""GetAuxiliaryGeomContactPtID(std::string const & geom_id, int index) -> std::string"""
        return _vsp.GetAuxiliaryGeomContactPtID(geom_id, index)
    
    @client_wrap
    def ReadAuxiliaryGeomCCEFile(self, geom_id, file_name):
        r"""ReadAuxiliaryGeomCCEFile(std::string const & geom_id, std::string const & file_name)"""
        return _vsp.ReadAuxiliaryGeomCCEFile(geom_id, file_name)
    
    @client_wrap
    def InitStackPreset(self, geom_id, preset):
        r"""InitStackPreset(std::string const & geom_id, int preset)"""
        return _vsp.InitStackPreset(geom_id, preset)
    
    @client_wrap
    def GetNumRoutingPts(self, routing_id):
        r"""GetNumRoutingPts(std::string const & routing_id) -> int"""
        return _vsp.GetNumRoutingPts(routing_id)
    
    @client_wrap
    def AddRoutingPt(self, routing_id, geom_id, surf_index):
        r"""AddRoutingPt(std::string const & routing_id, std::string const & geom_id, int surf_index) -> std::string"""
        return _vsp.AddRoutingPt(routing_id, geom_id, surf_index)
    
    @client_wrap
    def InsertRoutingPt(self, routing_id, index, geom_id, surf_index):
        r"""InsertRoutingPt(std::string const & routing_id, int index, std::string const & geom_id, int surf_index) -> std::string"""
        return _vsp.InsertRoutingPt(routing_id, index, geom_id, surf_index)
    
    @client_wrap
    def DelRoutingPt(self, routing_id, index):
        r"""DelRoutingPt(std::string const & routing_id, int index)"""
        return _vsp.DelRoutingPt(routing_id, index)
    
    @client_wrap
    def DelAllRoutingPt(self, routing_id):
        r"""DelAllRoutingPt(std::string const & routing_id)"""
        return _vsp.DelAllRoutingPt(routing_id)
    
    @client_wrap
    def MoveRoutingPt(self, routing_id, index, reorder_type):
        r"""MoveRoutingPt(std::string const & routing_id, int index, int reorder_type) -> int"""
        return _vsp.MoveRoutingPt(routing_id, index, reorder_type)
    
    @client_wrap
    def GetRoutingPtID(self, routing_id, index):
        r"""GetRoutingPtID(std::string const & routing_id, int index) -> std::string"""
        return _vsp.GetRoutingPtID(routing_id, index)
    
    @client_wrap
    def GetAllRoutingPtIds(self, routing_id):
        r"""GetAllRoutingPtIds(std::string const & routing_id) -> StringVector"""
        return _vsp.GetAllRoutingPtIds(routing_id)
    
    @client_wrap
    def GetRoutingPtParentID(self, pt_id):
        r"""GetRoutingPtParentID(std::string const & pt_id) -> std::string"""
        return _vsp.GetRoutingPtParentID(pt_id)
    
    @client_wrap
    def SetRoutingPtParentID(self, pt_id, parent_id):
        r"""SetRoutingPtParentID(std::string const & pt_id, std::string const & parent_id)"""
        return _vsp.SetRoutingPtParentID(pt_id, parent_id)
    
    @client_wrap
    def GetMainRoutingPtCoord(self, pt_id):
        r"""GetMainRoutingPtCoord(std::string const & pt_id) -> vec3d"""
        return _vsp.GetMainRoutingPtCoord(pt_id)
    
    @client_wrap
    def GetRoutingPtCoord(self, routing_id, index, symm_index):
        r"""GetRoutingPtCoord(std::string const & routing_id, int index, int symm_index) -> vec3d"""
        return _vsp.GetRoutingPtCoord(routing_id, index, symm_index)
    
    @client_wrap
    def GetAllRoutingPtCoords(self, routing_id, symm_index):
        r"""GetAllRoutingPtCoords(std::string const & routing_id, int symm_index) -> Vec3dVec"""
        return _vsp.GetAllRoutingPtCoords(routing_id, symm_index)
    
    @client_wrap
    def GetRoutingCurve(self, routing_id, symm_index):
        r"""GetRoutingCurve(std::string const & routing_id, int symm_index) -> Vec3dVec"""
        return _vsp.GetRoutingCurve(routing_id, symm_index)
    
    @client_wrap
    def ChangeBORXSecShape(self, bor_id, type):
        r"""ChangeBORXSecShape(std::string const & bor_id, int type)"""
        return _vsp.ChangeBORXSecShape(bor_id, type)
    
    @client_wrap
    def GetBORXSecShape(self, bor_id):
        r"""GetBORXSecShape(std::string const & bor_id) -> int"""
        return _vsp.GetBORXSecShape(bor_id)
    
    @client_wrap
    def ReadBORFileXSec(self, bor_id, file_name):
        r"""ReadBORFileXSec(std::string const & bor_id, std::string const & file_name) -> Vec3dVec"""
        return _vsp.ReadBORFileXSec(bor_id, file_name)
    
    @client_wrap
    def GetBORXSecPnts(self, bor_id):
        r"""GetBORXSecPnts(std::string const & bor_id) -> Vec3dVec"""
        return _vsp.GetBORXSecPnts(bor_id)
    
    @client_wrap
    def SetBORXSecPnts(self, bor_id, pnt_vec):
        r"""SetBORXSecPnts(std::string const & bor_id, Vec3dVec pnt_vec)"""
        return _vsp.SetBORXSecPnts(bor_id, pnt_vec)
    
    @client_wrap
    def ComputeBORXSecPnt(self, bor_id, fract):
        r"""ComputeBORXSecPnt(std::string const & bor_id, double fract) -> vec3d"""
        return _vsp.ComputeBORXSecPnt(bor_id, fract)
    
    @client_wrap
    def ComputeBORXSecTan(self, bor_id, fract):
        r"""ComputeBORXSecTan(std::string const & bor_id, double fract) -> vec3d"""
        return _vsp.ComputeBORXSecTan(bor_id, fract)
    
    @client_wrap
    def ReadBORFileAirfoil(self, bor_id, file_name):
        r"""ReadBORFileAirfoil(std::string const & bor_id, std::string const & file_name)"""
        return _vsp.ReadBORFileAirfoil(bor_id, file_name)
    
    @client_wrap
    def SetBORAirfoilUpperPnts(self, bor_id, up_pnt_vec):
        r"""SetBORAirfoilUpperPnts(std::string const & bor_id, Vec3dVec up_pnt_vec)"""
        return _vsp.SetBORAirfoilUpperPnts(bor_id, up_pnt_vec)
    
    @client_wrap
    def SetBORAirfoilLowerPnts(self, bor_id, low_pnt_vec):
        r"""SetBORAirfoilLowerPnts(std::string const & bor_id, Vec3dVec low_pnt_vec)"""
        return _vsp.SetBORAirfoilLowerPnts(bor_id, low_pnt_vec)
    
    @client_wrap
    def SetBORAirfoilPnts(self, bor_id, up_pnt_vec, low_pnt_vec):
        r"""SetBORAirfoilPnts(std::string const & bor_id, Vec3dVec up_pnt_vec, Vec3dVec low_pnt_vec)"""
        return _vsp.SetBORAirfoilPnts(bor_id, up_pnt_vec, low_pnt_vec)
    
    @client_wrap
    def GetBORAirfoilUpperPnts(self, bor_id):
        r"""GetBORAirfoilUpperPnts(std::string const & bor_id) -> Vec3dVec"""
        return _vsp.GetBORAirfoilUpperPnts(bor_id)
    
    @client_wrap
    def GetBORAirfoilLowerPnts(self, bor_id):
        r"""GetBORAirfoilLowerPnts(std::string const & bor_id) -> Vec3dVec"""
        return _vsp.GetBORAirfoilLowerPnts(bor_id)
    
    @client_wrap
    def GetBORUpperCSTCoefs(self, bor_id):
        r"""GetBORUpperCSTCoefs(std::string const & bor_id) -> DoubleVector"""
        return _vsp.GetBORUpperCSTCoefs(bor_id)
    
    @client_wrap
    def GetBORLowerCSTCoefs(self, bor_id):
        r"""GetBORLowerCSTCoefs(std::string const & bor_id) -> DoubleVector"""
        return _vsp.GetBORLowerCSTCoefs(bor_id)
    
    @client_wrap
    def GetBORUpperCSTDegree(self, bor_id):
        r"""GetBORUpperCSTDegree(std::string const & bor_id) -> int"""
        return _vsp.GetBORUpperCSTDegree(bor_id)
    
    @client_wrap
    def GetBORLowerCSTDegree(self, bor_id):
        r"""GetBORLowerCSTDegree(std::string const & bor_id) -> int"""
        return _vsp.GetBORLowerCSTDegree(bor_id)
    
    @client_wrap
    def SetBORUpperCST(self, bor_id, deg, coefs):
        r"""SetBORUpperCST(std::string const & bor_id, int deg, DoubleVector coefs)"""
        return _vsp.SetBORUpperCST(bor_id, deg, coefs)
    
    @client_wrap
    def SetBORLowerCST(self, bor_id, deg, coefs):
        r"""SetBORLowerCST(std::string const & bor_id, int deg, DoubleVector coefs)"""
        return _vsp.SetBORLowerCST(bor_id, deg, coefs)
    
    @client_wrap
    def PromoteBORCSTUpper(self, bor_id):
        r"""PromoteBORCSTUpper(std::string const & bor_id)"""
        return _vsp.PromoteBORCSTUpper(bor_id)
    
    @client_wrap
    def PromoteBORCSTLower(self, bor_id):
        r"""PromoteBORCSTLower(std::string const & bor_id)"""
        return _vsp.PromoteBORCSTLower(bor_id)
    
    @client_wrap
    def DemoteBORCSTUpper(self, bor_id):
        r"""DemoteBORCSTUpper(std::string const & bor_id)"""
        return _vsp.DemoteBORCSTUpper(bor_id)
    
    @client_wrap
    def DemoteBORCSTLower(self, bor_id):
        r"""DemoteBORCSTLower(std::string const & bor_id)"""
        return _vsp.DemoteBORCSTLower(bor_id)
    
    @client_wrap
    def FitBORAfCST(self, bor_id, deg):
        r"""FitBORAfCST(std::string const & bor_id, int deg)"""
        return _vsp.FitBORAfCST(bor_id, deg)
    
    @client_wrap
    def WriteBezierAirfoil(self, file_name, geom_id, foilsurf_u):
        r"""WriteBezierAirfoil(std::string const & file_name, std::string const & geom_id, double const & foilsurf_u)"""
        return _vsp.WriteBezierAirfoil(file_name, geom_id, foilsurf_u)
    
    @client_wrap
    def WriteSeligAirfoil(self, file_name, geom_id, foilsurf_u):
        r"""WriteSeligAirfoil(std::string const & file_name, std::string const & geom_id, double const & foilsurf_u)"""
        return _vsp.WriteSeligAirfoil(file_name, geom_id, foilsurf_u)
    
    @client_wrap
    def GetAirfoilCoordinates(self, geom_id, foilsurf_u):
        r"""GetAirfoilCoordinates(std::string const & geom_id, double const & foilsurf_u) -> Vec3dVec"""
        return _vsp.GetAirfoilCoordinates(geom_id, foilsurf_u)
    
    @client_wrap
    def EditXSecInitShape(self, xsec_id):
        r"""EditXSecInitShape(std::string const & xsec_id)"""
        return _vsp.EditXSecInitShape(xsec_id)
    
    @client_wrap
    def EditXSecConvertTo(self, xsec_id, newtype):
        r"""EditXSecConvertTo(std::string const & xsec_id, int const & newtype)"""
        return _vsp.EditXSecConvertTo(xsec_id, newtype)
    
    @client_wrap
    def GetEditXSecUVec(self, xsec_id):
        r"""GetEditXSecUVec(std::string const & xsec_id) -> DoubleVector"""
        return _vsp.GetEditXSecUVec(xsec_id)
    
    @client_wrap
    def GetEditXSecCtrlVec(self, xsec_id, non_dimensional=True):
        r"""GetEditXSecCtrlVec(std::string const & xsec_id, bool non_dimensional=True) -> Vec3dVec"""
        return _vsp.GetEditXSecCtrlVec(xsec_id, non_dimensional)
    
    @client_wrap
    def SetEditXSecPnts(self, xsec_id, u_vec, control_pts, r_vec):
        r"""SetEditXSecPnts(std::string const & xsec_id, DoubleVector u_vec, Vec3dVec control_pts, DoubleVector r_vec)"""
        return _vsp.SetEditXSecPnts(xsec_id, u_vec, control_pts, r_vec)
    
    @client_wrap
    def EditXSecDelPnt(self, xsec_id, indx):
        r"""EditXSecDelPnt(std::string const & xsec_id, int const & indx)"""
        return _vsp.EditXSecDelPnt(xsec_id, indx)
    
    @client_wrap
    def EditXSecSplit01(self, xsec_id, u):
        r"""EditXSecSplit01(std::string const & xsec_id, double const & u) -> int"""
        return _vsp.EditXSecSplit01(xsec_id, u)
    
    @client_wrap
    def MoveEditXSecPnt(self, xsec_id, indx, new_pnt):
        r"""MoveEditXSecPnt(std::string const & xsec_id, int const & indx, vec3d new_pnt)"""
        return _vsp.MoveEditXSecPnt(xsec_id, indx, new_pnt)
    
    @client_wrap
    def ConvertXSecToEdit(self, geom_id, indx=0):
        r"""ConvertXSecToEdit(std::string const & geom_id, int const & indx=0)"""
        return _vsp.ConvertXSecToEdit(geom_id, indx)
    
    @client_wrap
    def GetEditXSecFixedUVec(self, xsec_id):
        r"""GetEditXSecFixedUVec(std::string const & xsec_id) -> BoolVector"""
        return _vsp.GetEditXSecFixedUVec(xsec_id)
    
    @client_wrap
    def SetEditXSecFixedUVec(self, xsec_id, fixed_u_vec):
        r"""SetEditXSecFixedUVec(std::string const & xsec_id, BoolVector fixed_u_vec)"""
        return _vsp.SetEditXSecFixedUVec(xsec_id, fixed_u_vec)
    
    @client_wrap
    def ReparameterizeEditXSec(self, xsec_id):
        r"""ReparameterizeEditXSec(std::string const & xsec_id)"""
        return _vsp.ReparameterizeEditXSec(xsec_id)
    
    @client_wrap
    def GetNumSets(self, ):
        r"""GetNumSets() -> int"""
        return _vsp.GetNumSets()
    
    @client_wrap
    def SetSetName(self, index, name):
        r"""SetSetName(int index, std::string const & name)"""
        return _vsp.SetSetName(index, name)
    
    @client_wrap
    def GetSetName(self, index):
        r"""GetSetName(int index) -> std::string"""
        return _vsp.GetSetName(index)
    
    @client_wrap
    def GetGeomSetAtIndex(self, index):
        r"""GetGeomSetAtIndex(int index) -> StringVector"""
        return _vsp.GetGeomSetAtIndex(index)
    
    @client_wrap
    def GetGeomSet(self, name):
        r"""GetGeomSet(std::string const & name) -> StringVector"""
        return _vsp.GetGeomSet(name)
    
    @client_wrap
    def GetSetIndex(self, name):
        r"""GetSetIndex(std::string const & name) -> int"""
        return _vsp.GetSetIndex(name)
    
    @client_wrap
    def GetSetFlag(self, geom_id, set_index):
        r"""GetSetFlag(std::string const & geom_id, int set_index) -> bool"""
        return _vsp.GetSetFlag(geom_id, set_index)
    
    @client_wrap
    def SetSetFlag(self, geom_id, set_index, flag):
        r"""SetSetFlag(std::string const & geom_id, int set_index, bool flag)"""
        return _vsp.SetSetFlag(geom_id, set_index, flag)
    
    @client_wrap
    def CopyPasteSet(self, copyIndex, pasteIndex):
        r"""CopyPasteSet(int copyIndex, int pasteIndex)"""
        return _vsp.CopyPasteSet(copyIndex, pasteIndex)
    
    @client_wrap
    def GetBBoxSet(self, set):
        r"""GetBBoxSet(int set) -> bool"""
        return _vsp.GetBBoxSet(set)
    
    @client_wrap
    def GetScaleIndependentBBoxSet(self, set):
        r"""GetScaleIndependentBBoxSet(int set) -> bool"""
        return _vsp.GetScaleIndependentBBoxSet(set)
    
    @client_wrap
    def ScaleSet(self, set_index, scale):
        r"""ScaleSet(int set_index, double scale)"""
        return _vsp.ScaleSet(set_index, scale)
    
    @client_wrap
    def RotateSet(self, set_index, x_rot_deg, y_rot_deg, z_rot_deg):
        r"""RotateSet(int set_index, double x_rot_deg, double y_rot_deg, double z_rot_deg)"""
        return _vsp.RotateSet(set_index, x_rot_deg, y_rot_deg, z_rot_deg)
    
    @client_wrap
    def TranslateSet(self, set_index, translation_vec):
        r"""TranslateSet(int set_index, vec3d translation_vec)"""
        return _vsp.TranslateSet(set_index, translation_vec)
    
    @client_wrap
    def TransformSet(self, set_index, translation_vec, x_rot_deg, y_rot_deg, z_rot_deg, scale, scale_translations_flag):
        r"""TransformSet(int set_index, vec3d translation_vec, double x_rot_deg, double y_rot_deg, double z_rot_deg, double scale, bool scale_translations_flag)"""
        return _vsp.TransformSet(set_index, translation_vec, x_rot_deg, y_rot_deg, z_rot_deg, scale, scale_translations_flag)
    
    @client_wrap
    def ValidParm(self, id):
        r"""ValidParm(std::string const & id) -> bool"""
        return _vsp.ValidParm(id)
    
    @client_wrap
    def SetParmVal(self, *args):
        r"""
        SetParmVal(std::string const & parm_id, double val) -> double
        SetParmVal(std::string const & geom_id, std::string const & name, std::string const & group, double val) -> double
        """
        return _vsp.SetParmVal(*args)
    
    @client_wrap
    def SetParmValLimits(self, parm_id, val, lower_limit, upper_limit):
        r"""SetParmValLimits(std::string const & parm_id, double val, double lower_limit, double upper_limit) -> double"""
        return _vsp.SetParmValLimits(parm_id, val, lower_limit, upper_limit)
    
    @client_wrap
    def SetParmValUpdate(self, *args):
        r"""
        SetParmValUpdate(std::string const & parm_id, double val) -> double
        SetParmValUpdate(std::string const & geom_id, std::string const & parm_name, std::string const & parm_group_name, double val) -> double
        """
        return _vsp.SetParmValUpdate(*args)
    
    @client_wrap
    def GetParmVal(self, *args):
        r"""
        GetParmVal(std::string const & parm_id) -> double
        GetParmVal(std::string const & geom_id, std::string const & name, std::string const & group) -> double
        """
        return _vsp.GetParmVal(*args)
    
    @client_wrap
    def GetIntParmVal(self, parm_id):
        r"""GetIntParmVal(std::string const & parm_id) -> int"""
        return _vsp.GetIntParmVal(parm_id)
    
    @client_wrap
    def GetBoolParmVal(self, parm_id):
        r"""GetBoolParmVal(std::string const & parm_id) -> bool"""
        return _vsp.GetBoolParmVal(parm_id)
    
    @client_wrap
    def SetParmUpperLimit(self, parm_id, val):
        r"""SetParmUpperLimit(std::string const & parm_id, double val)"""
        return _vsp.SetParmUpperLimit(parm_id, val)
    
    @client_wrap
    def GetParmUpperLimit(self, parm_id):
        r"""GetParmUpperLimit(std::string const & parm_id) -> double"""
        return _vsp.GetParmUpperLimit(parm_id)
    
    @client_wrap
    def SetParmLowerLimit(self, parm_id, val):
        r"""SetParmLowerLimit(std::string const & parm_id, double val)"""
        return _vsp.SetParmLowerLimit(parm_id, val)
    
    @client_wrap
    def GetParmLowerLimit(self, parm_id):
        r"""GetParmLowerLimit(std::string const & parm_id) -> double"""
        return _vsp.GetParmLowerLimit(parm_id)
    
    @client_wrap
    def GetParmType(self, parm_id):
        r"""GetParmType(std::string const & parm_id) -> int"""
        return _vsp.GetParmType(parm_id)
    
    @client_wrap
    def GetParmName(self, parm_id):
        r"""GetParmName(std::string const & parm_id) -> std::string"""
        return _vsp.GetParmName(parm_id)
    
    @client_wrap
    def GetParmGroupName(self, parm_id):
        r"""GetParmGroupName(std::string const & parm_id) -> std::string"""
        return _vsp.GetParmGroupName(parm_id)
    
    @client_wrap
    def GetParmDisplayGroupName(self, parm_id):
        r"""GetParmDisplayGroupName(std::string const & parm_id) -> std::string"""
        return _vsp.GetParmDisplayGroupName(parm_id)
    
    @client_wrap
    def GetParmContainer(self, parm_id):
        r"""GetParmContainer(std::string const & parm_id) -> std::string"""
        return _vsp.GetParmContainer(parm_id)
    
    @client_wrap
    def SetParmDescript(self, parm_id, desc):
        r"""SetParmDescript(std::string const & parm_id, std::string const & desc)"""
        return _vsp.SetParmDescript(parm_id, desc)
    
    @client_wrap
    def GetParmDescript(self, parm_id):
        r"""GetParmDescript(std::string const & parm_id) -> std::string"""
        return _vsp.GetParmDescript(parm_id)
    
    @client_wrap
    def FindParm(self, parm_container_id, parm_name, group_name):
        r"""FindParm(std::string const & parm_container_id, std::string const & parm_name, std::string const & group_name) -> std::string"""
        return _vsp.FindParm(parm_container_id, parm_name, group_name)
    
    @client_wrap
    def FindContainers(self, ):
        r"""FindContainers() -> StringVector"""
        return _vsp.FindContainers()
    
    @client_wrap
    def FindContainersWithName(self, name):
        r"""FindContainersWithName(std::string const & name) -> StringVector"""
        return _vsp.FindContainersWithName(name)
    
    @client_wrap
    def FindContainer(self, name, index):
        r"""FindContainer(std::string const & name, int index) -> std::string"""
        return _vsp.FindContainer(name, index)
    
    @client_wrap
    def SetContainerName(self, parm_container_id, name):
        r"""SetContainerName(std::string const & parm_container_id, std::string const & name)"""
        return _vsp.SetContainerName(parm_container_id, name)
    
    @client_wrap
    def GetContainerName(self, parm_container_id):
        r"""GetContainerName(std::string const & parm_container_id) -> std::string"""
        return _vsp.GetContainerName(parm_container_id)
    
    @client_wrap
    def FindContainerGroupNames(self, parm_container_id):
        r"""FindContainerGroupNames(std::string const & parm_container_id) -> StringVector"""
        return _vsp.FindContainerGroupNames(parm_container_id)
    
    @client_wrap
    def FindContainerParmIDs(self, parm_container_id):
        r"""FindContainerParmIDs(std::string const & parm_container_id) -> StringVector"""
        return _vsp.FindContainerParmIDs(parm_container_id)
    
    @client_wrap
    def GetVehicleID(self, ):
        r"""GetVehicleID() -> std::string"""
        return _vsp.GetVehicleID()
    
    @client_wrap
    def GetNumUserParms(self, ):
        r"""GetNumUserParms() -> int"""
        return _vsp.GetNumUserParms()
    
    @client_wrap
    def GetNumPredefinedUserParms(self, ):
        r"""GetNumPredefinedUserParms() -> int"""
        return _vsp.GetNumPredefinedUserParms()
    
    @client_wrap
    def GetAllUserParms(self, ):
        r"""GetAllUserParms() -> StringVector"""
        return _vsp.GetAllUserParms()
    
    @client_wrap
    def GetUserParmContainer(self, ):
        r"""GetUserParmContainer() -> std::string"""
        return _vsp.GetUserParmContainer()
    
    @client_wrap
    def AddUserParm(self, type, name, group):
        r"""AddUserParm(int type, std::string const & name, std::string const & group) -> std::string"""
        return _vsp.AddUserParm(type, name, group)
    
    @client_wrap
    def DeleteUserParm(self, id):
        r"""DeleteUserParm(std::string const & id)"""
        return _vsp.DeleteUserParm(id)
    
    @client_wrap
    def DeleteAllUserParm(self, ):
        r"""DeleteAllUserParm()"""
        return _vsp.DeleteAllUserParm()
    
    @client_wrap
    def ComputeMinClearanceDistance(self, *args):
        r"""ComputeMinClearanceDistance(std::string const & geom_id, int set=SET_ALL, bool useMode=False, std::string const & modeID=std::string()) -> double"""
        return _vsp.ComputeMinClearanceDistance(*args)
    
    @client_wrap
    def SnapParm(self, *args):
        r"""SnapParm(std::string const & parm_id, double target_min_dist, bool inc_flag, int set=SET_ALL, bool useMode=False, std::string const & modeID=std::string()) -> double"""
        return _vsp.SnapParm(*args)
    
    @client_wrap
    def AddVarPresetGroup(self, group_name):
        r"""AddVarPresetGroup(std::string const & group_name) -> std::string"""
        return _vsp.AddVarPresetGroup(group_name)
    
    @client_wrap
    def AddVarPresetSetting(self, group_id, setting_name):
        r"""AddVarPresetSetting(std::string const & group_id, std::string const & setting_name) -> std::string"""
        return _vsp.AddVarPresetSetting(group_id, setting_name)
    
    @client_wrap
    def AddVarPresetParm(self, group_id, parm_id):
        r"""AddVarPresetParm(std::string const & group_id, std::string const & parm_id)"""
        return _vsp.AddVarPresetParm(group_id, parm_id)
    
    @client_wrap
    def DeleteAllVarPresetGroups(self, ):
        r"""DeleteAllVarPresetGroups()"""
        return _vsp.DeleteAllVarPresetGroups()
    
    @client_wrap
    def DeleteVarPresetGroup(self, group_id):
        r"""DeleteVarPresetGroup(std::string const & group_id)"""
        return _vsp.DeleteVarPresetGroup(group_id)
    
    @client_wrap
    def DeleteAllVarPresetSettings(self, group_id):
        r"""DeleteAllVarPresetSettings(std::string const & group_id)"""
        return _vsp.DeleteAllVarPresetSettings(group_id)
    
    @client_wrap
    def DeleteVarPresetSetting(self, group_id, setting_id):
        r"""DeleteVarPresetSetting(std::string const & group_id, std::string const & setting_id)"""
        return _vsp.DeleteVarPresetSetting(group_id, setting_id)
    
    @client_wrap
    def DeleteVarPresetParm(self, group_id, parm_id):
        r"""DeleteVarPresetParm(std::string const & group_id, std::string const & parm_id)"""
        return _vsp.DeleteVarPresetParm(group_id, parm_id)
    
    @client_wrap
    def SetVarPresetParmVal(self, group_id, setting_id, parm_id, parm_val):
        r"""SetVarPresetParmVal(std::string const & group_id, std::string const & setting_id, std::string const & parm_id, double parm_val)"""
        return _vsp.SetVarPresetParmVal(group_id, setting_id, parm_id, parm_val)
    
    @client_wrap
    def GetVarPresetParmVal(self, group_id, setting_id, parm_id):
        r"""GetVarPresetParmVal(std::string const & group_id, std::string const & setting_id, std::string const & parm_id) -> double"""
        return _vsp.GetVarPresetParmVal(group_id, setting_id, parm_id)
    
    @client_wrap
    def GetGroupName(self, group_id):
        r"""GetGroupName(std::string const & group_id) -> std::string"""
        return _vsp.GetGroupName(group_id)
    
    @client_wrap
    def GetSettingName(self, setting_id):
        r"""GetSettingName(std::string const & setting_id) -> std::string"""
        return _vsp.GetSettingName(setting_id)
    
    @client_wrap
    def SetGroupName(self, group_id, group_name):
        r"""SetGroupName(std::string const & group_id, std::string const & group_name)"""
        return _vsp.SetGroupName(group_id, group_name)
    
    @client_wrap
    def SetSettingName(self, setting_id, setting_name):
        r"""SetSettingName(std::string const & setting_id, std::string const & setting_name)"""
        return _vsp.SetSettingName(setting_id, setting_name)
    
    @client_wrap
    def GetVarPresetGroups(self, ):
        r"""GetVarPresetGroups() -> StringVector"""
        return _vsp.GetVarPresetGroups()
    
    @client_wrap
    def GetVarPresetSettings(self, group_id):
        r"""GetVarPresetSettings(std::string const & group_id) -> StringVector"""
        return _vsp.GetVarPresetSettings(group_id)
    
    @client_wrap
    def GetVarPresetParmIDs(self, group_id):
        r"""GetVarPresetParmIDs(std::string const & group_id) -> StringVector"""
        return _vsp.GetVarPresetParmIDs(group_id)
    
    @client_wrap
    def GetVarPresetParmVals(self, setting_id):
        r"""GetVarPresetParmVals(std::string const & setting_id) -> DoubleVector"""
        return _vsp.GetVarPresetParmVals(setting_id)
    
    @client_wrap
    def SetVarPresetParmVals(self, setting_id, parm_vals):
        r"""SetVarPresetParmVals(std::string const & setting_id, DoubleVector parm_vals)"""
        return _vsp.SetVarPresetParmVals(setting_id, parm_vals)
    
    @client_wrap
    def SaveVarPresetParmVals(self, group_id, setting_id):
        r"""SaveVarPresetParmVals(std::string const & group_id, std::string const & setting_id)"""
        return _vsp.SaveVarPresetParmVals(group_id, setting_id)
    
    @client_wrap
    def ApplyVarPresetSetting(self, group_id, setting_id):
        r"""ApplyVarPresetSetting(std::string const & group_id, std::string const & setting_id)"""
        return _vsp.ApplyVarPresetSetting(group_id, setting_id)
    
    @client_wrap
    def CreateAndAddMode(self, name, normal_set, degen_set):
        r"""CreateAndAddMode(std::string const & name, int normal_set, int degen_set) -> std::string"""
        return _vsp.CreateAndAddMode(name, normal_set, degen_set)
    
    @client_wrap
    def GetNumModes(self, ):
        r"""GetNumModes() -> int"""
        return _vsp.GetNumModes()
    
    @client_wrap
    def GetAllModes(self, ):
        r"""GetAllModes() -> StringVector"""
        return _vsp.GetAllModes()
    
    @client_wrap
    def DelMode(self, mid):
        r"""DelMode(std::string const & mid)"""
        return _vsp.DelMode(mid)
    
    @client_wrap
    def DelAllModes(self, ):
        r"""DelAllModes()"""
        return _vsp.DelAllModes()
    
    @client_wrap
    def GetModeName(self, mid):
        r"""GetModeName(std::string const & mid) -> std::string"""
        return _vsp.GetModeName(mid)
    
    @client_wrap
    def SetModeName(self, mid, name):
        r"""SetModeName(std::string const & mid, std::string const & name)"""
        return _vsp.SetModeName(mid, name)
    
    @client_wrap
    def ApplyModeSettings(self, mid):
        r"""ApplyModeSettings(std::string const & mid)"""
        return _vsp.ApplyModeSettings(mid)
    
    @client_wrap
    def ShowOnlyMode(self, mid):
        r"""ShowOnlyMode(std::string const & mid)"""
        return _vsp.ShowOnlyMode(mid)
    
    @client_wrap
    def ModeAddGroupSetting(self, mid, gid, sid):
        r"""ModeAddGroupSetting(std::string const & mid, std::string const & gid, std::string const & sid)"""
        return _vsp.ModeAddGroupSetting(mid, gid, sid)
    
    @client_wrap
    def ModeGetGroup(self, mid, indx):
        r"""ModeGetGroup(std::string const & mid, int indx) -> std::string"""
        return _vsp.ModeGetGroup(mid, indx)
    
    @client_wrap
    def ModeGetSetting(self, mid, indx):
        r"""ModeGetSetting(std::string const & mid, int indx) -> std::string"""
        return _vsp.ModeGetSetting(mid, indx)
    
    @client_wrap
    def ModeGetAllGroups(self, mid):
        r"""ModeGetAllGroups(std::string const & mid) -> StringVector"""
        return _vsp.ModeGetAllGroups(mid)
    
    @client_wrap
    def ModeGetAllSettings(self, mid):
        r"""ModeGetAllSettings(std::string const & mid) -> StringVector"""
        return _vsp.ModeGetAllSettings(mid)
    
    @client_wrap
    def RemoveGroupSetting(self, mid, indx):
        r"""RemoveGroupSetting(std::string const & mid, int indx)"""
        return _vsp.RemoveGroupSetting(mid, indx)
    
    @client_wrap
    def RemoveAllGroupSettings(self, mid):
        r"""RemoveAllGroupSettings(std::string const & mid)"""
        return _vsp.RemoveAllGroupSettings(mid)
    
    @client_wrap
    def SetPCurve(self, geom_id, pcurveid, tvec, valvec, newtype):
        r"""SetPCurve(std::string const & geom_id, int const & pcurveid, DoubleVector tvec, DoubleVector valvec, int const & newtype)"""
        return _vsp.SetPCurve(geom_id, pcurveid, tvec, valvec, newtype)
    
    @client_wrap
    def PCurveConvertTo(self, geom_id, pcurveid, newtype):
        r"""PCurveConvertTo(std::string const & geom_id, int const & pcurveid, int const & newtype)"""
        return _vsp.PCurveConvertTo(geom_id, pcurveid, newtype)
    
    @client_wrap
    def PCurveGetType(self, geom_id, pcurveid):
        r"""PCurveGetType(std::string const & geom_id, int const & pcurveid) -> int"""
        return _vsp.PCurveGetType(geom_id, pcurveid)
    
    @client_wrap
    def PCurveGetTVec(self, geom_id, pcurveid):
        r"""PCurveGetTVec(std::string const & geom_id, int const & pcurveid) -> DoubleVector"""
        return _vsp.PCurveGetTVec(geom_id, pcurveid)
    
    @client_wrap
    def PCurveGetValVec(self, geom_id, pcurveid):
        r"""PCurveGetValVec(std::string const & geom_id, int const & pcurveid) -> DoubleVector"""
        return _vsp.PCurveGetValVec(geom_id, pcurveid)
    
    @client_wrap
    def PCurveDeletePt(self, geom_id, pcurveid, indx):
        r"""PCurveDeletePt(std::string const & geom_id, int const & pcurveid, int const & indx)"""
        return _vsp.PCurveDeletePt(geom_id, pcurveid, indx)
    
    @client_wrap
    def PCurveSplit(self, geom_id, pcurveid, tsplit):
        r"""PCurveSplit(std::string const & geom_id, int const & pcurveid, double const & tsplit) -> int"""
        return _vsp.PCurveSplit(geom_id, pcurveid, tsplit)
    
    @client_wrap
    def ApproximateAllPropellerPCurves(self, geom_id):
        r"""ApproximateAllPropellerPCurves(std::string const & geom_id)"""
        return _vsp.ApproximateAllPropellerPCurves(geom_id)
    
    @client_wrap
    def ResetPropellerThickness(self, geom_id):
        r"""ResetPropellerThickness(std::string const & geom_id)"""
        return _vsp.ResetPropellerThickness(geom_id)
    
    @client_wrap
    def ResetPropellerThicknessCurve(self, geom_id):
        r"""ResetPropellerThicknessCurve(std::string const & geom_id)"""
        return _vsp.ResetPropellerThicknessCurve(geom_id)
    
    @client_wrap
    def AutoGroupVSPAEROControlSurfaces(self, ):
        r"""AutoGroupVSPAEROControlSurfaces()"""
        return _vsp.AutoGroupVSPAEROControlSurfaces()
    
    @client_wrap
    def AddCpSlice(self, cut_type, location):
        r"""AddCpSlice(int cut_type, double location) -> std::string"""
        return _vsp.AddCpSlice(cut_type, location)
    
    @client_wrap
    def GetNumCpSlices(self, ):
        r"""GetNumCpSlices() -> int"""
        return _vsp.GetNumCpSlices()
    
    @client_wrap
    def GetCpSliceID(self, slice_index):
        r"""GetCpSliceID(int slice_index) -> std::string"""
        return _vsp.GetCpSliceID(slice_index)
    
    @client_wrap
    def DelCpSlice(self, slice_index):
        r"""DelCpSlice(int slice_index)"""
        return _vsp.DelCpSlice(slice_index)
    
    @client_wrap
    def DeleteAllCpSlices(self, ):
        r"""DeleteAllCpSlices()"""
        return _vsp.DeleteAllCpSlices()
    
    @client_wrap
    def DeleteVSPAEROControlSurfaceGroup(self, CSGroupIndex):
        r"""DeleteVSPAEROControlSurfaceGroup(int CSGroupIndex)"""
        return _vsp.DeleteVSPAEROControlSurfaceGroup(CSGroupIndex)
    
    @client_wrap
    def CreateVSPAEROControlSurfaceGroup(self, ):
        r"""CreateVSPAEROControlSurfaceGroup() -> int"""
        return _vsp.CreateVSPAEROControlSurfaceGroup()
    
    @client_wrap
    def AddAllToVSPAEROControlSurfaceGroup(self, CSGroupIndex):
        r"""AddAllToVSPAEROControlSurfaceGroup(int CSGroupIndex)"""
        return _vsp.AddAllToVSPAEROControlSurfaceGroup(CSGroupIndex)
    
    @client_wrap
    def RemoveAllFromVSPAEROControlSurfaceGroup(self, CSGroupIndex):
        r"""RemoveAllFromVSPAEROControlSurfaceGroup(int CSGroupIndex)"""
        return _vsp.RemoveAllFromVSPAEROControlSurfaceGroup(CSGroupIndex)
    
    @client_wrap
    def GetActiveCSNameVec(self, CSGroupIndex):
        r"""GetActiveCSNameVec(int CSGroupIndex) -> StringVector"""
        return _vsp.GetActiveCSNameVec(CSGroupIndex)
    
    @client_wrap
    def GetCompleteCSNameVec(self, ):
        r"""GetCompleteCSNameVec() -> StringVector"""
        return _vsp.GetCompleteCSNameVec()
    
    @client_wrap
    def GetAvailableCSNameVec(self, CSGroupIndex):
        r"""GetAvailableCSNameVec(int CSGroupIndex) -> StringVector"""
        return _vsp.GetAvailableCSNameVec(CSGroupIndex)
    
    @client_wrap
    def SetVSPAEROControlGroupName(self, name, CSGroupIndex):
        r"""SetVSPAEROControlGroupName(std::string const & name, int CSGroupIndex)"""
        return _vsp.SetVSPAEROControlGroupName(name, CSGroupIndex)
    
    @client_wrap
    def GetVSPAEROControlGroupName(self, CSGroupIndex):
        r"""GetVSPAEROControlGroupName(int CSGroupIndex) -> std::string"""
        return _vsp.GetVSPAEROControlGroupName(CSGroupIndex)
    
    @client_wrap
    def AddSelectedToCSGroup(self, selected, CSGroupIndex):
        r"""AddSelectedToCSGroup(IntVector selected, int CSGroupIndex)"""
        return _vsp.AddSelectedToCSGroup(selected, CSGroupIndex)
    
    @client_wrap
    def RemoveSelectedFromCSGroup(self, selected, CSGroupIndex):
        r"""RemoveSelectedFromCSGroup(IntVector selected, int CSGroupIndex)"""
        return _vsp.RemoveSelectedFromCSGroup(selected, CSGroupIndex)
    
    @client_wrap
    def GetNumControlSurfaceGroups(self, ):
        r"""GetNumControlSurfaceGroups() -> int"""
        return _vsp.GetNumControlSurfaceGroups()
    
    @client_wrap
    def FindActuatorDisk(self, disk_index):
        r"""FindActuatorDisk(int disk_index) -> std::string"""
        return _vsp.FindActuatorDisk(disk_index)
    
    @client_wrap
    def GetNumActuatorDisks(self, ):
        r"""GetNumActuatorDisks() -> int"""
        return _vsp.GetNumActuatorDisks()
    
    @client_wrap
    def FindUnsteadyGroup(self, group_index):
        r"""FindUnsteadyGroup(int group_index) -> std::string"""
        return _vsp.FindUnsteadyGroup(group_index)
    
    @client_wrap
    def GetUnsteadyGroupName(self, group_index):
        r"""GetUnsteadyGroupName(int group_index) -> std::string"""
        return _vsp.GetUnsteadyGroupName(group_index)
    
    @client_wrap
    def GetUnsteadyGroupCompIDs(self, group_index):
        r"""GetUnsteadyGroupCompIDs(int group_index) -> StringVector"""
        return _vsp.GetUnsteadyGroupCompIDs(group_index)
    
    @client_wrap
    def GetUnsteadyGroupSurfIndexes(self, group_index):
        r"""GetUnsteadyGroupSurfIndexes(int group_index) -> IntVector"""
        return _vsp.GetUnsteadyGroupSurfIndexes(group_index)
    
    @client_wrap
    def GetNumUnsteadyGroups(self, ):
        r"""GetNumUnsteadyGroups() -> int"""
        return _vsp.GetNumUnsteadyGroups()
    
    @client_wrap
    def GetNumUnsteadyRotorGroups(self, ):
        r"""GetNumUnsteadyRotorGroups() -> int"""
        return _vsp.GetNumUnsteadyRotorGroups()
    
    @client_wrap
    def AddExcrescence(self, excresName, excresType, excresVal):
        r"""AddExcrescence(std::string const & excresName, int const & excresType, double const & excresVal)"""
        return _vsp.AddExcrescence(excresName, excresType, excresVal)
    
    @client_wrap
    def GetNumExcrescences(self, ):
        r"""GetNumExcrescences() -> int"""
        return _vsp.GetNumExcrescences()
    
    @client_wrap
    def DeleteAllExcrescences(self, ):
        r"""DeleteAllExcrescences()"""
        return _vsp.DeleteAllExcrescences()
    
    @client_wrap
    def DeleteExcrescence(self, index):
        r"""DeleteExcrescence(int const & index)"""
        return _vsp.DeleteExcrescence(index)
    
    @client_wrap
    def UpdateParasiteDrag(self, ):
        r"""UpdateParasiteDrag()"""
        return _vsp.UpdateParasiteDrag()
    
    @client_wrap
    def ExportParasiteDragToCSV(self, file_name):
        r"""ExportParasiteDragToCSV(std::string const & file_name) -> std::string"""
        return _vsp.ExportParasiteDragToCSV(file_name)
    
    @client_wrap
    def WriteAtmosphereCSVFile(self, file_name, atmos_type):
        r"""WriteAtmosphereCSVFile(std::string const & file_name, int const & atmos_type)"""
        return _vsp.WriteAtmosphereCSVFile(file_name, atmos_type)
    
    @client_wrap
    def CalcAtmosphere(self, alt, delta_temp, atmos_type):
        r"""CalcAtmosphere(double const & alt, double const & delta_temp, int const & atmos_type)"""
        return _vsp.CalcAtmosphere(alt, delta_temp, atmos_type)
    
    @client_wrap
    def WriteBodyFFCSVFile(self, file_name):
        r"""WriteBodyFFCSVFile(std::string const & file_name)"""
        return _vsp.WriteBodyFFCSVFile(file_name)
    
    @client_wrap
    def WriteWingFFCSVFile(self, file_name):
        r"""WriteWingFFCSVFile(std::string const & file_name)"""
        return _vsp.WriteWingFFCSVFile(file_name)
    
    @client_wrap
    def WriteCfEqnCSVFile(self, file_name):
        r"""WriteCfEqnCSVFile(std::string const & file_name)"""
        return _vsp.WriteCfEqnCSVFile(file_name)
    
    @client_wrap
    def WritePartialCfMethodCSVFile(self, file_name):
        r"""WritePartialCfMethodCSVFile(std::string const & file_name)"""
        return _vsp.WritePartialCfMethodCSVFile(file_name)
    
    @client_wrap
    def CompPnt01(self, geom_id, surf_indx, u, w):
        r"""CompPnt01(std::string const & geom_id, int const & surf_indx, double const & u, double const & w) -> vec3d"""
        return _vsp.CompPnt01(geom_id, surf_indx, u, w)
    
    @client_wrap
    def CompNorm01(self, geom_id, surf_indx, u, w):
        r"""CompNorm01(std::string const & geom_id, int const & surf_indx, double const & u, double const & w) -> vec3d"""
        return _vsp.CompNorm01(geom_id, surf_indx, u, w)
    
    @client_wrap
    def CompTanU01(self, geom_id, surf_indx, u, w):
        r"""CompTanU01(std::string const & geom_id, int const & surf_indx, double const & u, double const & w) -> vec3d"""
        return _vsp.CompTanU01(geom_id, surf_indx, u, w)
    
    @client_wrap
    def CompTanW01(self, geom_id, surf_indx, u, w):
        r"""CompTanW01(std::string const & geom_id, int const & surf_indx, double const & u, double const & w) -> vec3d"""
        return _vsp.CompTanW01(geom_id, surf_indx, u, w)
    
    @client_wrap
    def CompCurvature01(self, geom_id, surf_indx, u, w):
        r"""CompCurvature01(std::string const & geom_id, int const & surf_indx, double const & u, double const & w)"""
        return _vsp.CompCurvature01(geom_id, surf_indx, u, w)
    
    @client_wrap
    def ProjPnt01(self, geom_id, surf_indx, pt):
        r"""ProjPnt01(std::string const & geom_id, int const & surf_indx, vec3d pt) -> double"""
        return _vsp.ProjPnt01(geom_id, surf_indx, pt)
    
    @client_wrap
    def ProjPnt01I(self, geom_id, pt):
        r"""ProjPnt01I(std::string const & geom_id, vec3d pt) -> double"""
        return _vsp.ProjPnt01I(geom_id, pt)
    
    @client_wrap
    def ProjPnt01Guess(self, geom_id, surf_indx, pt, u0, w0):
        r"""ProjPnt01Guess(std::string const & geom_id, int const & surf_indx, vec3d pt, double const & u0, double const & w0) -> double"""
        return _vsp.ProjPnt01Guess(geom_id, surf_indx, pt, u0, w0)
    
    @client_wrap
    def AxisProjPnt01(self, geom_id, surf_indx, iaxis, pt):
        r"""AxisProjPnt01(std::string const & geom_id, int const & surf_indx, int const & iaxis, vec3d pt) -> double"""
        return _vsp.AxisProjPnt01(geom_id, surf_indx, iaxis, pt)
    
    @client_wrap
    def AxisProjPnt01I(self, geom_id, iaxis, pt):
        r"""AxisProjPnt01I(std::string const & geom_id, int const & iaxis, vec3d pt) -> double"""
        return _vsp.AxisProjPnt01I(geom_id, iaxis, pt)
    
    @client_wrap
    def AxisProjPnt01Guess(self, geom_id, surf_indx, iaxis, pt, u0, w0):
        r"""AxisProjPnt01Guess(std::string const & geom_id, int const & surf_indx, int const & iaxis, vec3d pt, double const & u0, double const & w0) -> double"""
        return _vsp.AxisProjPnt01Guess(geom_id, surf_indx, iaxis, pt, u0, w0)
    
    @client_wrap
    def InsideSurf(self, geom_id, surf_indx, pt):
        r"""InsideSurf(std::string const & geom_id, int const & surf_indx, vec3d pt) -> bool"""
        return _vsp.InsideSurf(geom_id, surf_indx, pt)
    
    @client_wrap
    def CompPntRST(self, geom_id, surf_indx, r, s, t):
        r"""CompPntRST(std::string const & geom_id, int const & surf_indx, double const & r, double const & s, double const & t) -> vec3d"""
        return _vsp.CompPntRST(geom_id, surf_indx, r, s, t)
    
    @client_wrap
    def FindRST(self, geom_id, surf_indx, pt):
        r"""FindRST(std::string const & geom_id, int const & surf_indx, vec3d pt) -> double"""
        return _vsp.FindRST(geom_id, surf_indx, pt)
    
    @client_wrap
    def FindRSTGuess(self, geom_id, surf_indx, pt, r0, s0, t0):
        r"""FindRSTGuess(std::string const & geom_id, int const & surf_indx, vec3d pt, double const & r0, double const & s0, double const & t0) -> double"""
        return _vsp.FindRSTGuess(geom_id, surf_indx, pt, r0, s0, t0)
    
    @client_wrap
    def ConvertRSTtoLMN(self, geom_id, surf_indx, r, s, t):
        r"""ConvertRSTtoLMN(std::string const & geom_id, int const & surf_indx, double const & r, double const & s, double const & t)"""
        return _vsp.ConvertRSTtoLMN(geom_id, surf_indx, r, s, t)
    
    @client_wrap
    def ConvertRtoL(self, geom_id, surf_indx, r):
        r"""ConvertRtoL(std::string const & geom_id, int const & surf_indx, double const & r)"""
        return _vsp.ConvertRtoL(geom_id, surf_indx, r)
    
    @client_wrap
    def ConvertLMNtoRST(self, geom_id, surf_indx, l, m, n):
        r"""ConvertLMNtoRST(std::string const & geom_id, int const & surf_indx, double const & l, double const & m, double const & n)"""
        return _vsp.ConvertLMNtoRST(geom_id, surf_indx, l, m, n)
    
    @client_wrap
    def ConvertLtoR(self, geom_id, surf_indx, l):
        r"""ConvertLtoR(std::string const & geom_id, int const & surf_indx, double const & l)"""
        return _vsp.ConvertLtoR(geom_id, surf_indx, l)
    
    @client_wrap
    def ConvertUtoEta(self, geom_id, u):
        r"""ConvertUtoEta(std::string const & geom_id, double const & u)"""
        return _vsp.ConvertUtoEta(geom_id, u)
    
    @client_wrap
    def ConvertEtatoU(self, geom_id, eta):
        r"""ConvertEtatoU(std::string const & geom_id, double const & eta)"""
        return _vsp.ConvertEtatoU(geom_id, eta)
    
    @client_wrap
    def CompVecPnt01(self, geom_id, surf_indx, u_in_vec, w_in_vec):
        r"""CompVecPnt01(std::string const & geom_id, int const & surf_indx, DoubleVector u_in_vec, DoubleVector w_in_vec) -> Vec3dVec"""
        return _vsp.CompVecPnt01(geom_id, surf_indx, u_in_vec, w_in_vec)
    
    @client_wrap
    def CompVecDegenPnt01(self, geom_id, surf_indx, degen_type, u_in_vec, w_in_vec):
        r"""CompVecDegenPnt01(std::string const & geom_id, int const & surf_indx, int const & degen_type, DoubleVector u_in_vec, DoubleVector w_in_vec) -> Vec3dVec"""
        return _vsp.CompVecDegenPnt01(geom_id, surf_indx, degen_type, u_in_vec, w_in_vec)
    
    @client_wrap
    def CompVecNorm01(self, geom_id, surf_indx, us, ws):
        r"""CompVecNorm01(std::string const & geom_id, int const & surf_indx, DoubleVector us, DoubleVector ws) -> Vec3dVec"""
        return _vsp.CompVecNorm01(geom_id, surf_indx, us, ws)
    
    @client_wrap
    def CompVecCurvature01(self, geom_id, surf_indx, us, ws):
        r"""CompVecCurvature01(std::string const & geom_id, int const & surf_indx, DoubleVector us, DoubleVector ws)"""
        return _vsp.CompVecCurvature01(geom_id, surf_indx, us, ws)
    
    @client_wrap
    def ProjVecPnt01(self, geom_id, surf_indx, pts):
        r"""ProjVecPnt01(std::string const & geom_id, int const & surf_indx, Vec3dVec pts)"""
        return _vsp.ProjVecPnt01(geom_id, surf_indx, pts)
    
    @client_wrap
    def ProjVecPnt01Guess(self, geom_id, surf_indx, pts, u0s, w0s):
        r"""ProjVecPnt01Guess(std::string const & geom_id, int const & surf_indx, Vec3dVec pts, DoubleVector u0s, DoubleVector w0s)"""
        return _vsp.ProjVecPnt01Guess(geom_id, surf_indx, pts, u0s, w0s)
    
    @client_wrap
    def AxisProjVecPnt01(self, geom_id, surf_indx, iaxis, pts):
        r"""AxisProjVecPnt01(std::string const & geom_id, int const & surf_indx, int const & iaxis, Vec3dVec pts)"""
        return _vsp.AxisProjVecPnt01(geom_id, surf_indx, iaxis, pts)
    
    @client_wrap
    def AxisProjVecPnt01Guess(self, geom_id, surf_indx, iaxis, pts, u0s, w0s):
        r"""AxisProjVecPnt01Guess(std::string const & geom_id, int const & surf_indx, int const & iaxis, Vec3dVec pts, DoubleVector u0s, DoubleVector w0s)"""
        return _vsp.AxisProjVecPnt01Guess(geom_id, surf_indx, iaxis, pts, u0s, w0s)
    
    @client_wrap
    def VecInsideSurf(self, geom_id, surf_indx, pts):
        r"""VecInsideSurf(std::string const & geom_id, int const & surf_indx, Vec3dVec pts) -> BoolVector"""
        return _vsp.VecInsideSurf(geom_id, surf_indx, pts)
    
    @client_wrap
    def CompVecPntRST(self, geom_id, surf_indx, r_in_vec, s_in_vec, t_in_vec):
        r"""CompVecPntRST(std::string const & geom_id, int const & surf_indx, DoubleVector r_in_vec, DoubleVector s_in_vec, DoubleVector t_in_vec) -> Vec3dVec"""
        return _vsp.CompVecPntRST(geom_id, surf_indx, r_in_vec, s_in_vec, t_in_vec)
    
    @client_wrap
    def FindRSTVec(self, geom_id, surf_indx, pts):
        r"""FindRSTVec(std::string const & geom_id, int const & surf_indx, Vec3dVec pts)"""
        return _vsp.FindRSTVec(geom_id, surf_indx, pts)
    
    @client_wrap
    def FindRSTVecGuess(self, geom_id, surf_indx, pts, r0s, s0s, t0s):
        r"""FindRSTVecGuess(std::string const & geom_id, int const & surf_indx, Vec3dVec pts, DoubleVector r0s, DoubleVector s0s, DoubleVector t0s)"""
        return _vsp.FindRSTVecGuess(geom_id, surf_indx, pts, r0s, s0s, t0s)
    
    @client_wrap
    def ConvertRSTtoLMNVec(self, geom_id, surf_indx, r_vec, s_vec, t_vec):
        r"""ConvertRSTtoLMNVec(std::string const & geom_id, int const & surf_indx, DoubleVector r_vec, DoubleVector s_vec, DoubleVector t_vec)"""
        return _vsp.ConvertRSTtoLMNVec(geom_id, surf_indx, r_vec, s_vec, t_vec)
    
    @client_wrap
    def ConvertLMNtoRSTVec(self, geom_id, surf_indx, l_vec, m_vec, n_vec):
        r"""ConvertLMNtoRSTVec(std::string const & geom_id, int const & surf_indx, DoubleVector l_vec, DoubleVector m_vec, DoubleVector n_vec)"""
        return _vsp.ConvertLMNtoRSTVec(geom_id, surf_indx, l_vec, m_vec, n_vec)
    
    @client_wrap
    def GetUWTess01(self, geom_id, surf_indx):
        r"""GetUWTess01(std::string const & geom_id, int const & surf_indx)"""
        return _vsp.GetUWTess01(geom_id, surf_indx)
    
    @client_wrap
    def ShowAllRulers(self, ):
        r"""ShowAllRulers()"""
        return _vsp.ShowAllRulers()
    
    @client_wrap
    def HideAllRulers(self, ):
        r"""HideAllRulers()"""
        return _vsp.HideAllRulers()
    
    @client_wrap
    def ShowAllProbes(self, ):
        r"""ShowAllProbes()"""
        return _vsp.ShowAllProbes()
    
    @client_wrap
    def HideAllProbes(self, ):
        r"""HideAllProbes()"""
        return _vsp.HideAllProbes()
    
    @client_wrap
    def ShowAllProtractors(self, ):
        r"""ShowAllProtractors()"""
        return _vsp.ShowAllProtractors()
    
    @client_wrap
    def HideAllProtractors(self, ):
        r"""HideAllProtractors()"""
        return _vsp.HideAllProtractors()
    
    @client_wrap
    def ShowAllRSTProbes(self, ):
        r"""ShowAllRSTProbes()"""
        return _vsp.ShowAllRSTProbes()
    
    @client_wrap
    def HideAllRSTProbes(self, ):
        r"""HideAllRSTProbes()"""
        return _vsp.HideAllRSTProbes()
    
    @client_wrap
    def AddProtractor(self, startgeomid, startsurfindx, startu, startw, midgeomid, midsurfindx, midu, midw, endgeomid, endsurfindx, endu, endw, name):
        r"""AddProtractor(std::string const & startgeomid, int startsurfindx, double startu, double startw, std::string const & midgeomid, int midsurfindx, double midu, double midw, std::string const & endgeomid, int endsurfindx, double endu, double endw, std::string const & name) -> std::string"""
        return _vsp.AddProtractor(startgeomid, startsurfindx, startu, startw, midgeomid, midsurfindx, midu, midw, endgeomid, endsurfindx, endu, endw, name)
    
    @client_wrap
    def GetAllProtractors(self, ):
        r"""GetAllProtractors() -> StringVector"""
        return _vsp.GetAllProtractors()
    
    @client_wrap
    def DelProtractor(self, id):
        r"""DelProtractor(std::string const & id)"""
        return _vsp.DelProtractor(id)
    
    @client_wrap
    def DeleteAllProtractors(self, ):
        r"""DeleteAllProtractors()"""
        return _vsp.DeleteAllProtractors()
    
    @client_wrap
    def AddRSTProbe(self, geomid, surfindx, r, s, t, name):
        r"""AddRSTProbe(std::string const & geomid, int surfindx, double r, double s, double t, std::string const & name) -> std::string"""
        return _vsp.AddRSTProbe(geomid, surfindx, r, s, t, name)
    
    @client_wrap
    def GetAllRSTProbes(self, ):
        r"""GetAllRSTProbes() -> StringVector"""
        return _vsp.GetAllRSTProbes()
    
    @client_wrap
    def DelRSTProbe(self, id):
        r"""DelRSTProbe(std::string const & id)"""
        return _vsp.DelRSTProbe(id)
    
    @client_wrap
    def DeleteAllRSTProbes(self, ):
        r"""DeleteAllRSTProbes()"""
        return _vsp.DeleteAllRSTProbes()
    
    @client_wrap
    def AddRuler(self, startgeomid, startsurfindx, startu, startw, endgeomid, endsurfindx, endu, endw, name):
        r"""AddRuler(std::string const & startgeomid, int startsurfindx, double startu, double startw, std::string const & endgeomid, int endsurfindx, double endu, double endw, std::string const & name) -> std::string"""
        return _vsp.AddRuler(startgeomid, startsurfindx, startu, startw, endgeomid, endsurfindx, endu, endw, name)
    
    @client_wrap
    def GetAllRulers(self, ):
        r"""GetAllRulers() -> StringVector"""
        return _vsp.GetAllRulers()
    
    @client_wrap
    def DelRuler(self, id):
        r"""DelRuler(std::string const & id)"""
        return _vsp.DelRuler(id)
    
    @client_wrap
    def DeleteAllRulers(self, ):
        r"""DeleteAllRulers()"""
        return _vsp.DeleteAllRulers()
    
    @client_wrap
    def AddProbe(self, geomid, surfindx, u, w, name):
        r"""AddProbe(std::string const & geomid, int surfindx, double u, double w, std::string const & name) -> std::string"""
        return _vsp.AddProbe(geomid, surfindx, u, w, name)
    
    @client_wrap
    def GetAllProbes(self, ):
        r"""GetAllProbes() -> StringVector"""
        return _vsp.GetAllProbes()
    
    @client_wrap
    def DelProbe(self, id):
        r"""DelProbe(std::string const & id)"""
        return _vsp.DelProbe(id)
    
    @client_wrap
    def DeleteAllProbes(self, ):
        r"""DeleteAllProbes()"""
        return _vsp.DeleteAllProbes()
    
    @client_wrap
    def CopyAirfoil(self, geom_id, index):
        r"""CopyAirfoil(std::string const & geom_id, int index)"""
        return _vsp.CopyAirfoil(geom_id, index)
    
    @client_wrap
    def PasteAirfoil(self, geom_id, index):
        r"""PasteAirfoil(std::string const & geom_id, int index)"""
        return _vsp.PasteAirfoil(geom_id, index)
    
    @client_wrap
    def ClearSkinning(self, geom_id, index=-1):
        r"""ClearSkinning(std::string const & geom_id, int index=-1)"""
        return _vsp.ClearSkinning(geom_id, index)
    
    @client_wrap
    def ResetGeomScale(self, geom_id):
        r"""ResetGeomScale(std::string const & geom_id)"""
        return _vsp.ResetGeomScale(geom_id)
    
    @client_wrap
    def AcceptGeomScale(self, geom_id):
        r"""AcceptGeomScale(std::string const & geom_id)"""
        return _vsp.AcceptGeomScale(geom_id)
    
    @client_wrap
    def GetPtCloudPnts(self, geom_id):
        r"""GetPtCloudPnts(std::string const & geom_id) -> Vec3dVec"""
        return _vsp.GetPtCloudPnts(geom_id)
    
    @client_wrap
    def CreatePtCloudGeom(self, geom_id):
        r"""CreatePtCloudGeom(std::string const & geom_id) -> std::string"""
        return _vsp.CreatePtCloudGeom(geom_id)
    
    @client_wrap
    def CreateNGonMeshGeom(self, geom_id):
        r"""CreateNGonMeshGeom(std::string const & geom_id) -> std::string"""
        return _vsp.CreateNGonMeshGeom(geom_id)
    
    @client_wrap
    def CreateConvexHull(self, geom_id):
        r"""CreateConvexHull(std::string const & geom_id) -> std::string"""
        return _vsp.CreateConvexHull(geom_id)
    
    @client_wrap
    def ProjectPtCloudPts(self, geom_id, target_geom_id, surf_index, dir_index):
        r"""ProjectPtCloudPts(std::string const & geom_id, std::string const & target_geom_id, int surf_index, int dir_index)"""
        return _vsp.ProjectPtCloudPts(geom_id, target_geom_id, surf_index, dir_index)
    
    @client_wrap
    def ShowSet(self, set_index):
        r"""ShowSet(int set_index)"""
        return _vsp.ShowSet(set_index)
    
    @client_wrap
    def NoShowSet(self, set_index):
        r"""NoShowSet(int set_index)"""
        return _vsp.NoShowSet(set_index)
    
    @client_wrap
    def ShowOnlySet(self, set_index):
        r"""ShowOnlySet(int set_index)"""
        return _vsp.ShowOnlySet(set_index)
    
    @client_wrap
    def AddParmLink(self, parm_a_id, parm_b_id):
        r"""AddParmLink(std::string const & parm_a_id, std::string const & parm_b_id) -> std::string"""
        return _vsp.AddParmLink(parm_a_id, parm_b_id)
    
    @client_wrap
    def GetNumParmLinks(self, ):
        r"""GetNumParmLinks() -> int"""
        return _vsp.GetNumParmLinks()
    
    @client_wrap
    def GetParmLinkID(self, index):
        r"""GetParmLinkID(int index) -> std::string"""
        return _vsp.GetParmLinkID(index)
    
    @client_wrap
    def GetParmLinkAParm(self, link_id):
        r"""GetParmLinkAParm(std::string const & link_id) -> std::string"""
        return _vsp.GetParmLinkAParm(link_id)
    
    @client_wrap
    def GetParmLinkBParm(self, link_id):
        r"""GetParmLinkBParm(std::string const & link_id) -> std::string"""
        return _vsp.GetParmLinkBParm(link_id)
    
    @client_wrap
    def SetParmLinkAParm(self, link_id, parm_id):
        r"""SetParmLinkAParm(std::string const & link_id, std::string const & parm_id)"""
        return _vsp.SetParmLinkAParm(link_id, parm_id)
    
    @client_wrap
    def SetParmLinkBParm(self, link_id, parm_id):
        r"""SetParmLinkBParm(std::string const & link_id, std::string const & parm_id)"""
        return _vsp.SetParmLinkBParm(link_id, parm_id)
    
    @client_wrap
    def DelParmLink(self, link_id):
        r"""DelParmLink(std::string const & link_id)"""
        return _vsp.DelParmLink(link_id)
    
    @client_wrap
    def DelAllParmLinks(self, ):
        r"""DelAllParmLinks()"""
        return _vsp.DelAllParmLinks()
    
    @client_wrap
    def SortParmLinksByA(self, ):
        r"""SortParmLinksByA()"""
        return _vsp.SortParmLinksByA()
    
    @client_wrap
    def SortParmLinksByB(self, ):
        r"""SortParmLinksByB()"""
        return _vsp.SortParmLinksByB()
    
    @client_wrap
    def LinkAllComp(self, parm_a_id, parm_b_id):
        r"""LinkAllComp(std::string const & parm_a_id, std::string const & parm_b_id) -> bool"""
        return _vsp.LinkAllComp(parm_a_id, parm_b_id)
    
    @client_wrap
    def LinkAllGroup(self, parm_a_id, parm_b_id):
        r"""LinkAllGroup(std::string const & parm_a_id, std::string const & parm_b_id) -> bool"""
        return _vsp.LinkAllGroup(parm_a_id, parm_b_id)
    
    @client_wrap
    def GetParmLinkOffsetFlag(self, link_id):
        r"""GetParmLinkOffsetFlag(std::string const & link_id) -> bool"""
        return _vsp.GetParmLinkOffsetFlag(link_id)
    
    @client_wrap
    def SetParmLinkOffsetFlag(self, link_id, flag):
        r"""SetParmLinkOffsetFlag(std::string const & link_id, bool flag)"""
        return _vsp.SetParmLinkOffsetFlag(link_id, flag)
    
    @client_wrap
    def GetParmLinkScaleFlag(self, link_id):
        r"""GetParmLinkScaleFlag(std::string const & link_id) -> bool"""
        return _vsp.GetParmLinkScaleFlag(link_id)
    
    @client_wrap
    def SetParmLinkScaleFlag(self, link_id, flag):
        r"""SetParmLinkScaleFlag(std::string const & link_id, bool flag)"""
        return _vsp.SetParmLinkScaleFlag(link_id, flag)
    
    @client_wrap
    def GetParmLinkLowerLimitFlag(self, link_id):
        r"""GetParmLinkLowerLimitFlag(std::string const & link_id) -> bool"""
        return _vsp.GetParmLinkLowerLimitFlag(link_id)
    
    @client_wrap
    def SetParmLinkLowerLimitFlag(self, link_id, flag):
        r"""SetParmLinkLowerLimitFlag(std::string const & link_id, bool flag)"""
        return _vsp.SetParmLinkLowerLimitFlag(link_id, flag)
    
    @client_wrap
    def GetParmLinkUpperLimitFlag(self, link_id):
        r"""GetParmLinkUpperLimitFlag(std::string const & link_id) -> bool"""
        return _vsp.GetParmLinkUpperLimitFlag(link_id)
    
    @client_wrap
    def SetParmLinkUpperLimitFlag(self, link_id, flag):
        r"""SetParmLinkUpperLimitFlag(std::string const & link_id, bool flag)"""
        return _vsp.SetParmLinkUpperLimitFlag(link_id, flag)
    
    @client_wrap
    def GetAdvLinkNames(self, ):
        r"""GetAdvLinkNames() -> StringVector"""
        return _vsp.GetAdvLinkNames()
    
    @client_wrap
    def GetLinkIndex(self, name):
        r"""GetLinkIndex(std::string const & name) -> int"""
        return _vsp.GetLinkIndex(name)
    
    @client_wrap
    def GetAdvLinkName(self, index):
        r"""GetAdvLinkName(int index) -> std::string"""
        return _vsp.GetAdvLinkName(index)
    
    @client_wrap
    def SetAdvLinkName(self, index, name):
        r"""SetAdvLinkName(int index, std::string const & name)"""
        return _vsp.SetAdvLinkName(index, name)
    
    @client_wrap
    def DelAdvLink(self, index):
        r"""DelAdvLink(int index)"""
        return _vsp.DelAdvLink(index)
    
    @client_wrap
    def DelAllAdvLinks(self, ):
        r"""DelAllAdvLinks()"""
        return _vsp.DelAllAdvLinks()
    
    @client_wrap
    def AddAdvLink(self, name):
        r"""AddAdvLink(std::string const & name)"""
        return _vsp.AddAdvLink(name)
    
    @client_wrap
    def AddAdvLinkInput(self, index, parm_id, var_name):
        r"""AddAdvLinkInput(int index, std::string const & parm_id, std::string const & var_name)"""
        return _vsp.AddAdvLinkInput(index, parm_id, var_name)
    
    @client_wrap
    def AddAdvLinkOutput(self, index, parm_id, var_name):
        r"""AddAdvLinkOutput(int index, std::string const & parm_id, std::string const & var_name)"""
        return _vsp.AddAdvLinkOutput(index, parm_id, var_name)
    
    @client_wrap
    def DelAllAdvLinkInputs(self, index):
        r"""DelAllAdvLinkInputs(int index)"""
        return _vsp.DelAllAdvLinkInputs(index)
    
    @client_wrap
    def DelAllAdvLinkOutputs(self, index):
        r"""DelAllAdvLinkOutputs(int index)"""
        return _vsp.DelAllAdvLinkOutputs(index)
    
    @client_wrap
    def DelAdvLinkInput(self, index, var_name):
        r"""DelAdvLinkInput(int index, std::string const & var_name)"""
        return _vsp.DelAdvLinkInput(index, var_name)
    
    @client_wrap
    def DelAdvLinkOutput(self, index, var_name):
        r"""DelAdvLinkOutput(int index, std::string const & var_name)"""
        return _vsp.DelAdvLinkOutput(index, var_name)
    
    @client_wrap
    def GetAdvLinkInputNames(self, index):
        r"""GetAdvLinkInputNames(int index) -> StringVector"""
        return _vsp.GetAdvLinkInputNames(index)
    
    @client_wrap
    def GetAdvLinkInputParms(self, index):
        r"""GetAdvLinkInputParms(int index) -> StringVector"""
        return _vsp.GetAdvLinkInputParms(index)
    
    @client_wrap
    def GetAdvLinkOutputNames(self, index):
        r"""GetAdvLinkOutputNames(int index) -> StringVector"""
        return _vsp.GetAdvLinkOutputNames(index)
    
    @client_wrap
    def GetAdvLinkOutputParms(self, index):
        r"""GetAdvLinkOutputParms(int index) -> StringVector"""
        return _vsp.GetAdvLinkOutputParms(index)
    
    @client_wrap
    def ValidateAdvLinkParms(self, index):
        r"""ValidateAdvLinkParms(int index) -> bool"""
        return _vsp.ValidateAdvLinkParms(index)
    
    @client_wrap
    def SetAdvLinkCode(self, index, code):
        r"""SetAdvLinkCode(int index, std::string const & code)"""
        return _vsp.SetAdvLinkCode(index, code)
    
    @client_wrap
    def GetAdvLinkCode(self, index):
        r"""GetAdvLinkCode(int index) -> std::string"""
        return _vsp.GetAdvLinkCode(index)
    
    @client_wrap
    def SearchReplaceAdvLinkCode(self, index, _from, to):
        r"""SearchReplaceAdvLinkCode(int index, std::string const & _from, std::string const & to)"""
        return _vsp.SearchReplaceAdvLinkCode(index, _from, to)
    
    @client_wrap
    def BuildAdvLinkScript(self, index):
        r"""BuildAdvLinkScript(int index) -> bool"""
        return _vsp.BuildAdvLinkScript(index)
    
    @client_wrap
    def WriteAdvLinkCodeFile(self, index, file_name):
        r"""WriteAdvLinkCodeFile(int index, std::string const & file_name)"""
        return _vsp.WriteAdvLinkCodeFile(index, file_name)
    
    @client_wrap
    def ReorderAdvLinkInput(self, index, var_name, reorder_type):
        r"""ReorderAdvLinkInput(int index, std::string const & var_name, int reorder_type)"""
        return _vsp.ReorderAdvLinkInput(index, var_name, reorder_type)
    
    @client_wrap
    def ReorderAdvLinkOutput(self, index, var_name, reorder_type):
        r"""ReorderAdvLinkOutput(int index, std::string const & var_name, int reorder_type)"""
        return _vsp.ReorderAdvLinkOutput(index, var_name, reorder_type)
    
    @client_wrap
    def ReadAdvLinkCodeFile(self, index, file_name):
        r"""ReadAdvLinkCodeFile(int index, std::string const & file_name)"""
        return _vsp.ReadAdvLinkCodeFile(index, file_name)
    
    @client_wrap
    def SortAdvLinkInputsVar(self, index):
        r"""SortAdvLinkInputsVar(int index)"""
        return _vsp.SortAdvLinkInputsVar(index)
    
    @client_wrap
    def SortAdvLinkInputsCGP(self, index):
        r"""SortAdvLinkInputsCGP(int index)"""
        return _vsp.SortAdvLinkInputsCGP(index)
    
    @client_wrap
    def SortAdvLinkOutputsVar(self, index):
        r"""SortAdvLinkOutputsVar(int index)"""
        return _vsp.SortAdvLinkOutputsVar(index)
    
    @client_wrap
    def SortAdvLinkOutputsCGP(self, index):
        r"""SortAdvLinkOutputsCGP(int index)"""
        return _vsp.SortAdvLinkOutputsCGP(index)
    
    @client_wrap
    def AddVec3D(self, INOUT, x, y, z):
        r"""AddVec3D(Vec3dVec INOUT, double x, double y, double z)"""
        return _vsp.AddVec3D(INOUT, x, y, z)
    # Register vec3d in _vsp:
        
    @client_wrap
    def signed_angle(self, a, b, ref):
        r"""signed_angle(vec3d a, vec3d b, vec3d ref) -> double"""
        return _vsp.signed_angle(a, b, ref)
    
    @client_wrap
    def radius_of_circle(self, p1, p2, p3):
        r"""radius_of_circle(vec3d p1, vec3d p2, vec3d p3) -> double"""
        return _vsp.radius_of_circle(p1, p2, p3)
    
    @client_wrap
    def center_of_circle(self, p1, p2, p3, center):
        r"""center_of_circle(vec3d p1, vec3d p2, vec3d p3, vec3d center)"""
        return _vsp.center_of_circle(p1, p2, p3, center)
    
    @client_wrap
    def triangle_plane_intersect_test(self, org, norm, p1, p2, p3):
        r"""triangle_plane_intersect_test(vec3d org, vec3d norm, vec3d p1, vec3d p2, vec3d p3) -> bool"""
        return _vsp.triangle_plane_intersect_test(org, norm, p1, p2, p3)
    
    @client_wrap
    def triangle_plane_minimum_dist(self, org, norm, p1, p2, p3, pa, pb):
        r"""triangle_plane_minimum_dist(vec3d org, vec3d norm, vec3d p1, vec3d p2, vec3d p3, vec3d pa, vec3d pb) -> double"""
        return _vsp.triangle_plane_minimum_dist(org, norm, p1, p2, p3, pa, pb)
    
    @client_wrap
    def triangle_plane_maximum_dist(self, org, norm, p1, p2, p3, pa, pb):
        r"""triangle_plane_maximum_dist(vec3d org, vec3d norm, vec3d p1, vec3d p2, vec3d p3, vec3d pa, vec3d pb) -> double"""
        return _vsp.triangle_plane_maximum_dist(org, norm, p1, p2, p3, pa, pb)
    
    @client_wrap
    def plane_plane_intersection(self, p0, n0, p1, n1, p, v):
        r"""plane_plane_intersection(vec3d p0, vec3d n0, vec3d p1, vec3d n1, vec3d p, vec3d v) -> bool"""
        return _vsp.plane_plane_intersection(p0, n0, p1, n1, p, v)
    
    @client_wrap
    def angle_pnt_2_plane(self, *args):
        r"""
        angle_pnt_2_plane(vec3d ptplane, vec3d norm, vec3d ptaxis, vec3d axis, vec3d pt, int ccw, vec3d prot) -> double
        angle_pnt_2_plane(vec3d ptplane, vec3d norm, vec3d ptaxis, vec3d axis, vec3d pt, int ccw, vec3d prot1, vec3d prot2, double & theta1, double & theta2)
        """
        return _vsp.angle_pnt_2_plane(*args)
    
    @client_wrap
    def signed_dist_pnt_2_plane(self, org, norm, pnt):
        r"""signed_dist_pnt_2_plane(vec3d org, vec3d norm, vec3d pnt) -> double"""
        return _vsp.signed_dist_pnt_2_plane(org, norm, pnt)
    
    @client_wrap
    def dist_pnt_2_plane(self, org, norm, pnt):
        r"""dist_pnt_2_plane(vec3d org, vec3d norm, vec3d pnt) -> double"""
        return _vsp.dist_pnt_2_plane(org, norm, pnt)
    
    @client_wrap
    def dist_pnt_2_line(self, line_pt1, line_pt2, pnt):
        r"""dist_pnt_2_line(vec3d line_pt1, vec3d line_pt2, vec3d pnt) -> double"""
        return _vsp.dist_pnt_2_line(line_pt1, line_pt2, pnt)
    
    @client_wrap
    def dist_pnt_2_ray(self, line_pt1, dir_unit_vec, pnt):
        r"""dist_pnt_2_ray(vec3d line_pt1, vec3d dir_unit_vec, vec3d pnt) -> double"""
        return _vsp.dist_pnt_2_ray(line_pt1, dir_unit_vec, pnt)
    
    @client_wrap
    def proj_u_on_v(self, u, v):
        r"""proj_u_on_v(vec3d u, vec3d v) -> vec3d"""
        return _vsp.proj_u_on_v(u, v)
    
    @client_wrap
    def proj_pnt_on_ray(self, *args):
        r"""
        proj_pnt_on_ray(vec3d line_pt1, vec3d line_dir, vec3d pnt) -> vec3d
        proj_pnt_on_ray(vec3d line_pt1, vec3d line_dir, vec3d pnt, double & t) -> vec3d
        """
        return _vsp.proj_pnt_on_ray(*args)
    
    @client_wrap
    def proj_pnt_on_line(self, line_pt1, line_pt2, pnt):
        r"""proj_pnt_on_line(vec3d line_pt1, vec3d line_pt2, vec3d pnt) -> vec3d"""
        return _vsp.proj_pnt_on_line(line_pt1, line_pt2, pnt)
    
    @client_wrap
    def proj_pnt_to_plane(self, org, plane_ln1, plane_ln2, pnt):
        r"""proj_pnt_to_plane(vec3d org, vec3d plane_ln1, vec3d plane_ln2, vec3d pnt) -> vec3d"""
        return _vsp.proj_pnt_to_plane(org, plane_ln1, plane_ln2, pnt)
    
    @client_wrap
    def proj_vec_to_plane(self, vec, norm):
        r"""proj_vec_to_plane(vec3d vec, vec3d norm) -> vec3d"""
        return _vsp.proj_vec_to_plane(vec, norm)
    
    @client_wrap
    def tri_seg_intersect(self, A, B, C, D, E, u, w, t):
        r"""tri_seg_intersect(vec3d A, vec3d B, vec3d C, vec3d D, vec3d E, double & u, double & w, double & t) -> int"""
        return _vsp.tri_seg_intersect(A, B, C, D, E, u, w, t)
    
    @client_wrap
    def tri_ray_intersect(self, A, B, C, D, E, u, w, t):
        r"""tri_ray_intersect(vec3d A, vec3d B, vec3d C, vec3d D, vec3d E, double & u, double & w, double & t) -> int"""
        return _vsp.tri_ray_intersect(A, B, C, D, E, u, w, t)
    
    @client_wrap
    def plane_ray_intersect(self, *args):
        r"""
        plane_ray_intersect(vec3d A, vec3d B, vec3d C, vec3d D, vec3d E, double & t) -> int
        plane_ray_intersect(vec3d orig, vec3d norm, vec3d D, vec3d E, double & t) -> int
        """
        return _vsp.plane_ray_intersect(*args)
    
    @client_wrap
    def ray_ray_intersect(self, A, B, C, D, int_pnt1, int_pnt2):
        r"""ray_ray_intersect(vec3d A, vec3d B, vec3d C, vec3d D, vec3d int_pnt1, vec3d int_pnt2) -> int"""
        return _vsp.ray_ray_intersect(A, B, C, D, int_pnt1, int_pnt2)
    
    @client_wrap
    def tetra_volume(self, A, B, C):
        r"""tetra_volume(vec3d A, vec3d B, vec3d C) -> double"""
        return _vsp.tetra_volume(A, B, C)
    
    @client_wrap
    def area(self, A, B, C):
        r"""area(vec3d A, vec3d B, vec3d C) -> double"""
        return _vsp.area(A, B, C)
    
    @client_wrap
    def dist3D_Segment_to_Segment(self, *args):
        r"""
        dist3D_Segment_to_Segment(vec3d S1P0, vec3d S1P1, vec3d S2P0, vec3d S2P1) -> double
        dist3D_Segment_to_Segment(vec3d S1P0, vec3d S1P1, vec3d S2P0, vec3d S2P1, double * Lt, vec3d Ln, double * St, vec3d Sn) -> double
        """
        return _vsp.dist3D_Segment_to_Segment(*args)
    
    @client_wrap
    def nearSegSeg(self, L0, L1, S0, S1, Lt, Ln, St, Sn):
        r"""nearSegSeg(vec3d L0, vec3d L1, vec3d S0, vec3d S1, double * Lt, vec3d Ln, double * St, vec3d Sn) -> double"""
        return _vsp.nearSegSeg(L0, L1, S0, S1, Lt, Ln, St, Sn)
    
    @client_wrap
    def pointLineDistSquared(self, p, lp0, lp1, t, pon):
        r"""pointLineDistSquared(vec3d p, vec3d lp0, vec3d lp1, double & t, vec3d pon) -> double"""
        return _vsp.pointLineDistSquared(p, lp0, lp1, t, pon)
    
    @client_wrap
    def pointSegDistSquared(self, p, sp0, sp1, t, pon):
        r"""pointSegDistSquared(vec3d p, vec3d sp0, vec3d sp1, double & t, vec3d pon) -> double"""
        return _vsp.pointSegDistSquared(p, sp0, sp1, t, pon)
    
    @client_wrap
    def point_on_line(self, lp0, lp1, t):
        r"""point_on_line(vec3d lp0, vec3d lp1, double const & t) -> vec3d"""
        return _vsp.point_on_line(lp0, lp1, t)
    
    @client_wrap
    def MapToPlane(self, p, planeOrig, planeVec1, planeVec2):
        r"""MapToPlane(vec3d p, vec3d planeOrig, vec3d planeVec1, vec3d planeVec2) -> vec2d"""
        return _vsp.MapToPlane(p, planeOrig, planeVec1, planeVec2)
    
    @client_wrap
    def MapFromPlane(self, uw, planeOrig, planeVec1, planeVec2):
        r"""MapFromPlane(vec2d uw, vec3d planeOrig, vec3d planeVec1, vec3d planeVec2) -> vec3d"""
        return _vsp.MapFromPlane(uw, planeOrig, planeVec1, planeVec2)
    
    @client_wrap
    def plane_half_space(self, planeOrig, planeNorm, pnt):
        r"""plane_half_space(vec3d planeOrig, vec3d planeNorm, vec3d pnt) -> int"""
        return _vsp.plane_half_space(planeOrig, planeNorm, pnt)
    
    @client_wrap
    def line_line_intersect(self, p1, p2, p3, p4, s, t):
        r"""line_line_intersect(vec3d p1, vec3d p2, vec3d p3, vec3d p4, double * s, double * t) -> bool"""
        return _vsp.line_line_intersect(p1, p2, p3, p4, s, t)
    
    @client_wrap
    def RotateArbAxis(self, p, theta, r):
        r"""RotateArbAxis(vec3d p, double theta, vec3d r) -> vec3d"""
        return _vsp.RotateArbAxis(p, theta, r)
    
    @client_wrap
    def PtInTri(self, v0, v1, v2, p):
        r"""PtInTri(vec3d v0, vec3d v1, vec3d v2, vec3d p) -> bool"""
        return _vsp.PtInTri(v0, v1, v2, p)
    
    @client_wrap
    def BarycentricWeights(self, v0, v1, v2, p):
        r"""BarycentricWeights(vec3d v0, vec3d v1, vec3d v2, vec3d p) -> vec3d"""
        return _vsp.BarycentricWeights(v0, v1, v2, p)
    
    @client_wrap
    def BilinearWeights(self, p0, p1, p, weights):
        r"""BilinearWeights(vec3d p0, vec3d p1, vec3d p, DoubleVector weights)"""
        return _vsp.BilinearWeights(p0, p1, p, weights)
    
    @client_wrap
    def tri_tri_min_dist(self, v0, v1, v2, v3, v4, v5, p1, p2):
        r"""tri_tri_min_dist(vec3d v0, vec3d v1, vec3d v2, vec3d v3, vec3d v4, vec3d v5, vec3d p1, vec3d p2) -> double"""
        return _vsp.tri_tri_min_dist(v0, v1, v2, v3, v4, v5, p1, p2)
    
    @client_wrap
    def pnt_tri_min_dist(self, v0, v1, v2, pnt, pnearest):
        r"""pnt_tri_min_dist(vec3d v0, vec3d v1, vec3d v2, vec3d pnt, vec3d pnearest) -> double"""
        return _vsp.pnt_tri_min_dist(v0, v1, v2, pnt, pnearest)
    
    @client_wrap
    def slerp(self, a, b, t):
        r"""slerp(vec3d a, vec3d b, double const & t) -> vec3d"""
        return _vsp.slerp(a, b, t)
    
    @client_wrap
    def printpt(self, v):
        r"""printpt(vec3d v)"""
        return _vsp.printpt(v)
    
    @client_wrap
    def ToSpherical(self, v):
        r"""ToSpherical(vec3d v) -> vec3d"""
        return _vsp.ToSpherical(v)
    
    @client_wrap
    def ToSpherical2(self, v, vdet):
        r"""ToSpherical2(vec3d v, vec3d vdet) -> vec3d"""
        return _vsp.ToSpherical2(v, vdet)
    
    @client_wrap
    def ToCartesian(self, v):
        r"""ToCartesian(vec3d v) -> vec3d"""
        return _vsp.ToCartesian(v)
    
    @client_wrap
    def FitPlane(self, pts, cen, norm):
        r"""FitPlane(Vec3dVec pts, vec3d cen, vec3d norm)"""
        return _vsp.FitPlane(pts, cen, norm)
    
    
    @client_wrap
    def to_string(self, v):
        r"""to_string(vec3d v) -> std::string"""
        return _vsp.to_string(v)
    
    @client_wrap
    def compsum(self, x):
        r"""compsum(Vec3dVec x) -> vec3d"""
        return _vsp.compsum(x)
    
    @client_wrap
    def raySphereIntersectionFromInside(self, p, v, c, r, x):
        r"""raySphereIntersectionFromInside(vec3d p, vec3d v, vec3d c, double r, vec3d x) -> bool"""
        return _vsp.raySphereIntersectionFromInside(p, v, c, r, x)
    # Register vec2d in _vsp:
    
    @client_wrap
    def dist(self, *args):
        r"""
        dist(vec3d a, vec3d b) -> double
        dist(vec2d a, vec2d b) -> double
        """
        return _vsp.dist(*args)
    
    @client_wrap
    def dist_squared(self, *args):
        r"""
        dist_squared(vec3d a, vec3d b) -> double
        dist_squared(vec2d a, vec2d b) -> double
        """
        return _vsp.dist_squared(*args)
    
    @client_wrap
    def cross(self, *args):
        r"""
        cross(vec3d a, vec3d b) -> vec3d
        cross(vec2d a, vec2d b) -> double
        """
        return _vsp.cross(*args)
    
    @client_wrap
    def dot(self, *args):
        r"""
        dot(vec3d a, vec3d b) -> double
        dot(vec2d a, vec2d b) -> double
        """
        return _vsp.dot(*args)
    
    @client_wrap
    def angle(self, *args):
        r"""
        angle(vec3d a, vec3d b) -> double
        angle(vec2d a, vec2d b) -> double
        """
        return _vsp.angle(*args)
    
    @client_wrap
    def cos_angle(self, *args):
        r"""
        cos_angle(vec3d a, vec3d b) -> double
        cos_angle(vec2d a, vec2d b) -> double
        """
        return _vsp.cos_angle(*args)
    
    @client_wrap
    def seg_seg_intersect(self, pnt_A, pnt_B, pnt_C, pnt_D, int_pnt, t1, t2):
        r"""seg_seg_intersect(vec2d pnt_A, vec2d pnt_B, vec2d pnt_C, vec2d pnt_D, vec2d int_pnt, double & t1, double & t2) -> int"""
        return _vsp.seg_seg_intersect(pnt_A, pnt_B, pnt_C, pnt_D, int_pnt, t1, t2)
    
    @client_wrap
    def proj_pnt_on_line_seg(self, *args):
        r"""
        proj_pnt_on_line_seg(vec3d line_pt1, vec3d line_pt2, vec3d pnt) -> vec3d
        proj_pnt_on_line_seg(vec2d line_A, vec2d line_B, vec2d pnt) -> vec2d
        """
        return _vsp.proj_pnt_on_line_seg(*args)
    
    @client_wrap
    def proj_pnt_on_line_u(self, line_A, line_B, pnt):
        r"""proj_pnt_on_line_u(vec2d line_A, vec2d line_B, vec2d pnt) -> double"""
        return _vsp.proj_pnt_on_line_u(line_A, line_B, pnt)
    
    @client_wrap
    def encode(self, x_min, y_min, x_max, y_max, pnt, code):
        r"""encode(double x_min, double y_min, double x_max, double y_max, vec2d pnt, int [4] code)"""
        return _vsp.encode(x_min, y_min, x_max, y_max, pnt, code)
    
    @client_wrap
    def clip_seg_rect(self, x_min, y_min, x_max, y_max, pnt1, pnt2, visible):
        r"""clip_seg_rect(double x_min, double y_min, double x_max, double y_max, vec2d pnt1, vec2d pnt2, int & visible)"""
        return _vsp.clip_seg_rect(x_min, y_min, x_max, y_max, pnt1, pnt2, visible)
    
    @client_wrap
    def PointInPolygon(self, R, pnts):
        r"""PointInPolygon(vec2d R, std::vector< vec2d,std::allocator< vec2d > > const & pnts) -> bool"""
        return _vsp.PointInPolygon(R, pnts)
    
    @client_wrap
    def det(self, p0, p1, offset):
        r"""det(vec2d p0, vec2d p1, vec2d offset) -> double"""
        return _vsp.det(p0, p1, offset)
    
    @client_wrap
    def poly_area(self, *args):
        r"""
        poly_area(Vec3dVec pnt_vec) -> double
        poly_area(std::vector< vec2d,std::allocator< vec2d > > const & pnt_vec) -> double
        """
        return _vsp.poly_area(*args)
    
    @client_wrap
    def poly_centroid(self, pnt_vec):
        r"""poly_centroid(std::vector< vec2d,std::allocator< vec2d > > const & pnt_vec) -> vec2d"""
        return _vsp.poly_centroid(pnt_vec)
    
    @client_wrap
    def orient2d(self, p0, p1, p):
        r"""orient2d(vec2d p0, vec2d p1, vec2d p) -> double"""
        return _vsp.orient2d(p0, p1, p)
    
    @client_wrap
    def bi_lin_interp(self, p0, p1, p2, p3, s, t, p):
        r"""bi_lin_interp(vec2d p0, vec2d p1, vec2d p2, vec2d p3, double s, double t, vec2d p)"""
        return _vsp.bi_lin_interp(p0, p1, p2, p3, s, t, p)
    
    @client_wrap
    def inverse_bi_lin_interp(self, p0, p1, p2, p3, p, s, t, s2, t2):
        r"""inverse_bi_lin_interp(vec2d p0, vec2d p1, vec2d p2, vec2d p3, vec2d p, double & s, double & t, double & s2, double & t2) -> int"""
        return _vsp.inverse_bi_lin_interp(p0, p1, p2, p3, p, s, t, s2, t2)
    
    # Register Matrix4d in _vsp:
    

    # function to send and receive data from the facade server
    def _send_receive(self, func_name, args, kwargs):
        b_data = pack_data([func_name, args, kwargs], True)
        self._sock.sendall(b_data)
        result = None
        b_result = []
        while True:
            packet = self._sock.recv(202400)
            if not packet: break
            b_result.append(packet)
            try:
                result = unpack_data(b_result)
                break
            except:
                pass
        if isinstance(result, list) and result[0] == "error":
            sys.excepthook = _exception_hook
            raise Exception(result[1])
        return result

    def IsFacade(self):
        """
        Returns True if the facade API is in use.


        .. code-block:: python

            is_facade = IsFacade()

        """

        return True
    def IsGUIRunning(self):
        """
        Returns True if the GUI event loop is running.


        .. code-block:: python

            is_gui_active = IsGUIRunning()

        """

        return self._send_receive('IsGUIRunning', [], {})

    def _run_func(self, func, *args, **kwargs):
        try:
            kwargs['vsp_instance'] = self
            return func(*args, **kwargs)
        except TypeError:
            kwargs.pop("vsp_instance")
            return func(*args, **kwargs)

    def _close_server(self):
        try:
            self._proc.terminate()
            self._proc.wait(1)
        except:
            pass
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
            self._sock.close()
            self._sock.detach()
            del self._sock
        except:
            pass
        try:
            del self.t
        except:
            pass
    def __del__(self):
        try:
            self._proc.terminate()
            self._proc.wait(1)
        except:
            pass
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
            self._sock.close()
            self._sock.detach()
            del self._sock
        except:
            pass
        try:
            del self.t
        except:
            pass


class _server_controller():
    def __init__(self) -> None:
        if openvsp_config.FACADE_PRINT_LEVEL > 0: print("OpenVSP server controller initialized")
        self._name_to_server = {}
        self._port_to_name = {}
        self._name_to_port = {}
        self.name_num = 1
        self.funcs = []
    def start_vsp_instance(self, name=None, port=-1) -> _vsp_server:

        if not name:
            name = f"default_name_{self.name_num}"
            while name in self._name_to_server:
                self.name_num += 1
                name = f"default_name_{self.name_num}"

        assert isinstance(name,str), "Name must be a string"
        assert not name in self._name_to_server, f"Server with name {name} already exists"
        assert not port in self._port_to_name, f"Server with port {port} already exists"
        self._name_to_server[name] = new_server = _vsp_server(name, self.funcs, port=port)
        self._port_to_name[new_server.port] = name
        self._name_to_port[name] = new_server.port

        return new_server

    def get_vsp_instance(self, server):
        try:
            if isinstance(server, str):
                return self._name_to_server[server]
            elif server in self._name_to_server.values():
                return server
        except:
            pass
        print("OpenVSP Warning: Could not find vsp_instance, returning singleton")
        return None

    def stop_vsp_instance(self, name=None, port=None):
        assert name or port, "please specify a name or a port"
        if port:
            server_name = self._port_to_name[port]
            server_port = port
        elif name:
            server_port = self._name_to_port[name]
            server_name = name
        if server_name == "vsp_singleton":
            print("OpenVSP can't close vsp_singleton")
            return
        self._port_to_name.pop(server_port)
        self._name_to_port.pop(server_name)
        self._name_to_server[server_name]._close_server()
        del self._name_to_server[server_name]

    def set_functions(self, funcs):
        self.funcs = funcs

from openvsp.vsp import ErrorObj
from openvsp.vsp import ErrorMgrSingleton
from openvsp.vsp import vec3d
from openvsp.vsp import Matrix4d
vsp_servers = _server_controller()
