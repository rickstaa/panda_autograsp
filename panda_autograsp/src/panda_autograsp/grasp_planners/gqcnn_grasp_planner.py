#!/usr/bin/env python
"""This module uses the `BerkleyAutomation GQCNN
<https://berkeleyautomation.github.io/gqcnn>`_
grasp detector to compute a valid grasp out of RBG-D sensor data.
"""

# Make script both python2 and python3 compatible
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

try:
    input = raw_input
except NameError:
    pass

# Main python packages
import math
import os
import time
import sys
import json
import numpy as np
import matplotlib.pyplot as plt

from perception import (
    Kinect2Sensor,
    ColorImage,
    BinaryImage,
    RgbdImage,
    Kinect2PacketPipelineMode,
)
from perception.image import imresize
from visualization import Visualizer2D as vis
from gqcnn.grasping import (
    Grasp2D,
    SuctionPoint2D,
    CrossEntropyRobustGraspingPolicy,
    RgbdImageState,
    FullyConvolutionalGraspingPolicyParallelJaw,
    FullyConvolutionalGraspingPolicySuction,
)
from gqcnn.utils import NoValidGraspsException, GripperMode
from autolab_core import YamlConfig

# Panda_autograsp modules, msgs and srvs
from panda_autograsp.functions import download_model
from panda_autograsp import Logger

# Set right matplotlib backend
# Needed in order to show images inside imported modules
plt.switch_backend("TkAgg")

# Delete logger format set by the autolab_core
Logger.clear_root()

# Create Logger
mod_logger = Logger.get_logger(__name__)

#################################################
# Script parameters #############################
#################################################

# Setup PacketPipeline mode dictionary
packet_modes = {}
for (attr, value) in Kinect2PacketPipelineMode.__dict__.items():
    if "__" not in attr:
        packet_modes.update({value: attr})

# Read panda_autograsp configuration file
MAIN_CFG = YamlConfig(
    os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.realpath(__file__)), "../../../cfg/main_config.yaml"
        )
    )
)

# Get settings out of main_cfg
GRASP_SOLUTION = "gqcnn"
DEFAULT_MODEL = MAIN_CFG["grasp_detection"][GRASP_SOLUTION]["defaults"]["model"]
MODELS_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "../../..",
        MAIN_CFG["main"]["models_dir"],
    )
)
DOWNLOAD_SCRIPT_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "../../../..",
        "gqcnn/scripts/downloads/models/download_models.sh",
    )
)


#################################################
# GQCNN grasp class #############################
#################################################
class GQCNNGrasp(object):
    """Class for storing the computed grasps. This class is a trimmed
    down version of the original :py:class:`gqcnn.grasping.policy.policy.GraspAction`.
    """

    def __init__(self):
        self.pose = object()
        self.q_value = 0.0
        self.grasp_type = 1
        self.center_px = [0, 0]
        self.angle = 0.0
        self.depth = 0.0
        self.thumbnail = object()

    @property
    def PARALLEL_JAW(self):
        """:py:obj:`PARALLEL_JAW` Specifies parallel jaw gripper type information
        """
        return 0

    @property
    def SUCTION(self):
        """:py:obj:`SUCTION` Specifies suction jaw gripper type information
        """
        return 1


#################################################
# Grasp planner Class ###########################
#################################################
class GraspPlanner(object):
    """ Class used to compute the grasp pose out of the RGB-D data.

    Attributes:
        cfg : :py:obj:`autolab_core.YamlConfig`
            The grasp ``yaml`` configuration file.
        policy_cfg: :py:obj:`autolab_core.YamlConfig`
            The policy ``yaml`` configuration file.
        sensor_type : :py:obj:`str`
            The type of sensor that is used.
        gqcnn_model : :py:obj:`str`
            The GQCNN model that is used.
        gripper_mode : :py:obj:`unicode`
            The gripper that is used.
        sensor : :py:obj:`perception.Kinect2Sensor`
            The sensor object.
        grasping_policy : :py:obj:`gqcnn.CrossEntropyRobustGraspingPolicy`
            The grasp policy.
        min_width: :py:obj:`int`
            The minimum allowed image width.
        min_height : :py:obj:`int`
            The minimum allowed image height.
    """

    def __init__(self, model=DEFAULT_MODEL, sensor_type="kinectv2"):
        """

        Parameters
        ----------------
        model : :py:obj:`str`, optional
            Name of the grasp detection model, by default DEFAULT_MODEL.
        sensor_type: :py:obj:`str`, optional
            The name of the RGBD sensor, by default kinectv2.
        """

        # Load model and policy configuration files
        self._get_cfg(model)

        # Create grasping policy
        mod_logger.info("Creating Grasping Policy")
        try:
            if (
                "cross_entropy"
                == MAIN_CFG["grasp_detection"]["gqcnn"]["parameters"]["available"][
                    model
                ]
            ):
                self.grasping_policy = CrossEntropyRobustGraspingPolicy(self.policy_cfg)
            elif (
                "fully_conv"
                == MAIN_CFG["grasp_detection"]["gqcnn"]["parameters"]["available"][
                    model
                ]
            ):
                if "pj" in model.lower():
                    self.grasping_policy = FullyConvolutionalGraspingPolicyParallelJaw(
                        self.policy_cfg
                    )
                elif "suction" in model.lower():
                    self.grasping_policy = FullyConvolutionalGraspingPolicySuction(
                        self.policy_cfg
                    )
        except KeyError:
            mod_logger.info(
                "The %s model of the %s policy is not yet implemented."
                % (model, "gqcnn")
            )
            sys.exit(0)

        # Create usefull class properties
        self.sensor_type = sensor_type
        self.gqcnn_model = self.grasping_policy.grasp_quality_fn.config[
            "gqcnn_model"
        ].split("/")[-1]
        self.gripper_mode = self.grasping_policy._grasp_quality_fn._gqcnn.gripper_mode

        # Initiate RBGD sensor
        if self.sensor_type == "kinectv2":
            self.sensor = Kinect2Sensor()
        else:
            mod_logger.error(
                "Unfortunately the "
                + self.sensor_type
                + " camera is not yet supported."
            )
            sys.exit(0)  # Exit script

        # Set minimum sensor input dimensions
        pad = max(
            math.ceil(
                np.sqrt(2) * (float(self.cfg["policy"]["metric"]["crop_width"]) / 2)
            ),
            math.ceil(
                np.sqrt(2) * (float(self.cfg["policy"]["metric"]["crop_height"]) / 2)
            ),
        )
        self.min_width = 2 * pad + self.cfg["policy"]["metric"]["crop_width"]
        self.min_height = 2 * pad + self.cfg["policy"]["metric"]["crop_height"]

    def _get_cfg(self, model):
        """Function retrieves the model and policy configuration files for a given model.

        Parameters
        ----------
        model : :py:obj:`str`
            Model used for the grasp detection CNN.
        """

        # Download CNN model if not present
        model_dir = os.path.join(MODELS_PATH, model)
        if not os.path.exists(model_dir):
            mod_logger.warning(
                "The "
                + model
                + " model was not found in the models folder. This model is required "
                "to continue."
            )
            while True:
                prompt_result = input("Do you want to download this model now? [Y/n] ")

                # Check user input
                # If yes download sample
                if prompt_result.lower() in ["y", "yes"]:
                    val = download_model(model, MODELS_PATH, DOWNLOAD_SCRIPT_PATH)
                    if not val == 0:  # Check if download was successful
                        shutdown_msg = (
                            "Shutting down %s node because grasp model could not "
                            "downloaded." % (model)
                        )
                        mod_logger.warning(shutdown_msg)
                        sys.exit(0)
                    else:
                        break
                elif prompt_result.lower() in ["n", "no"]:
                    shutdown_msg = (
                        "Shutting down %s node because grasp model is not downloaded."
                        % (model)
                    )
                    mod_logger.warning(shutdown_msg)
                    sys.exit(0)
                elif prompt_result == "":
                    download_model(model, MODELS_PATH, DOWNLOAD_SCRIPT_PATH)
                    if not val == 0:  # Check if download was successful
                        shutdown_msg = (
                            "Shutting down %s node because grasp model could not "
                            "downloaded." % (model)
                        )
                        mod_logger.warning(shutdown_msg)
                        sys.exit(0)
                    else:
                        break
                else:
                    print(
                        prompt_result
                        + " is not a valid response please answer with Y or N "
                        "to continue."
                    )

        # Retrieve model related configuration values
        self.model_config = json.load(open(os.path.join(model_dir, "config.json"), "r"))
        try:
            gqcnn_config = self.model_config["gqcnn"]
            gripper_mode = gqcnn_config["gripper_mode"]
        except KeyError:
            gqcnn_config = self.model_config["gqcnn_config"]
            input_data_mode = gqcnn_config["input_data_mode"]
            if input_data_mode == "tf_image":
                gripper_mode = GripperMode.LEGACY_PARALLEL_JAW
            elif input_data_mode == "tf_image_suction":
                gripper_mode = GripperMode.LEGACY_SUCTION
            elif input_data_mode == "suction":
                gripper_mode = GripperMode.SUCTION
            elif input_data_mode == "multi_suction":
                gripper_mode = GripperMode.MULTI_SUCTION
            elif input_data_mode == "parallel_jaw":
                gripper_mode = GripperMode.PARALLEL_JAW
            else:
                raise ValueError(
                    "Input data mode {} not supported!".format(input_data_mode)
                )

        # Get the policy based config parameters
        config_filename = os.path.abspath(
            os.path.join(
                os.path.dirname(os.path.realpath(__file__)),
                "../../../..",
                "gqcnn/cfg/examples/gqcnn_suction.yaml",
            )
        )
        if (
            gripper_mode == GripperMode.LEGACY_PARALLEL_JAW
            or gripper_mode == GripperMode.PARALLEL_JAW
        ):
            config_filename = os.path.abspath(
                os.path.join(
                    os.path.dirname(os.path.realpath(__file__)),
                    "../../../..",
                    "gqcnn/cfg/examples/gqcnn_pj.yaml",
                )
            )

        # Get CNN and Policy files
        self.cfg = YamlConfig(config_filename)
        self.policy_cfg = self.cfg["policy"]
        self.policy_cfg["metric"]["gqcnn_model"] = model_dir

    def start(self):
        """Calls the _start_sensor method."""
        start_msg = (
            "Starting "
            + self.gqcnn_model
            + " grasp planner for a "
            + self.gripper_mode
            + " robot."
        )
        mod_logger.info(start_msg)

        # Start sensor
        self._start_sensor()

    def _start_sensor(self):
        """Start sensor."""

        # Start sensor
        self.sensor.start()

        # Print packet pipeline information
        mod_logger.info(
            "Packet pipeline: " + packet_modes[self.sensor._packet_pipeline_mode]
        )

    def read_images(self, skip_registration=False):
        """Retrieves data frames from the sensor.

        Parameters
        ----------
        skip_registration : :py:obj:`bool`, optional
            If True, the registration step is skipped, by default False.

        Returns
        -------
        :py:obj:`tuple` of :py:obj:`perception.ColorImage`,
        :py:obj:`perception.DepthImage`, :py:obj:`perception.IrImage`,
        :py:obj:`numpy.ndarray`
            The ColorImage, DepthImage, and IrImage of the current frame.

        Raises
        ------
        :py:obj:`exceptions.RuntimeError`
            If the Kinect stream is not running.
        """

        # Get color, depth and ir image frames
        if self.sensor_type == "kinectv2":
            color_im, depth_im, ir_im = self.sensor.frames(skip_registration)

        # Validate whether depth and color images are the same size
        if color_im.height != depth_im.height or color_im.width != depth_im.width:
            msg = (
                "Color image and depth image must be the same shape! Color"
                " is %d x %d but depth is %d x %d"
            ) % (color_im.height, color_im.width, depth_im.height, depth_im.width)
            mod_logger.warning(msg)
        if color_im.height < self.min_height or color_im.width < self.min_width:
            msg = (
                "Color image is too small! Must be at least %d x %d"
                " resolution but the requested image is only %d x %d"
            ) % (self.min_height, self.min_width, color_im.height, color_im.width)
            mod_logger.warning(msg)

        # Return color and depth frames
        return color_im, depth_im, ir_im

    def plan_grasp(self):
        """Samples the image and computes possible grasps.

        Returns
        -------
        :py:class:`GQCNNGrasp`
            Computed optimal grasp.
        """
        # Get color, depth and ir image frames
        color_im, depth_im, _ = self.read_images()
        return self._plan_grasp(color_im, depth_im, self.sensor.ir_intrinsics)

    def plan_grasp_bb(self, bounding_box=None):
        """Samples the image and computes possible grasps while taking
        into account the supplied bounding box.

        Parameters
        ----------
        bounding_box : :py:obj:`perception.RgbdDetection`, optional
            A bounding box specifying the object, by default None

        Returns
        -------
        :py:class:`GQCNNGrasp`
            Computed optimal grasp.
        """

        # Get color, depth and ir image frames
        color_im, depth_im, _ = self.read_images()
        return self._plan_grasp(
            color_im, depth_im, self.sensor.ir_intrinsics, bounding_box=bounding_box
        )

    def plan_grasp_segmask(self, segmask):
        """Samples the image and computes possible grasps while taking
        into account the supplied segmask.

        Parameters
        ----------
        segmask : :py:obj:`perception.BinaryImage`
            Binary segmask of detected object

        Returns
        -------
        :py:obj:`GQCNNGrasp`
            Computed optimal grasp.
        """

        # Get color, depth and ir image frames
        color_im, depth_im, _ = self.read_images()

        # Check whether segmask
        raw_segmask = segmask
        try:
            segmask = BinaryImage(raw_segmask, frame=self.sensor.ir_intrinsics.frame)
        except Exception as plan_grasp_segmask_exception:
            mod_logger.warning(plan_grasp_segmask_exception)

        # Validate whether image and segmask are the same size
        if color_im.height != segmask.height or color_im.width != segmask.width:
            msg = (
                "Images and segmask must be the same shape! Color image is"
                " %d x %d but segmask is %d x %d"
            ) % (color_im.height, color_im.width, segmask.height, segmask.width)
            mod_logger.warning(msg)

        return self._plan_grasp(
            color_im, depth_im, self.sensor.ir_intrinsics, segmask=segmask
        )

    def _plan_grasp(
        self, color_im, depth_im, camera_intr, bounding_box=None, segmask=None
    ):
        """Plan grasp function that is effectually computes the grasp.

        Parameters
        ----------
        color_im : :py:obj:`perception.ColorImage`
            Color image.
        depth_im : :py:obj:`perception.DepthImage`
            Depth image.
        camera_intr : :obj;`perception.CameraIntrinsics`
            Intrinsic camera object.
        bounding_box : :py:obj:`gqcnn.RgbdImageState`, optional
            A bounding box specifying the object, by default None
        segmask : :py:obj:`perception.BinaryImage`
            Binary segmask of detected object


        Returns
        -------
        GQCNNGrasp
            Computed grasp.
        """
        mod_logger.info("Planning Grasp")

        # Inpaint images
        color_im = color_im.inpaint(rescale_factor=self.cfg["inpaint_rescale_factor"])
        depth_im = depth_im.inpaint(rescale_factor=self.cfg["inpaint_rescale_factor"])

        # Init segmask
        if segmask is None:
            segmask = BinaryImage(
                255 * np.ones(depth_im.shape).astype(np.uint8), frame=color_im.frame
            )

        # Visualize
        if MAIN_CFG["vis"]["grasp"]["figs"]["color_image"]:
            vis.imshow(color_im)
            vis.title("Color image")
            vis.show()
        if MAIN_CFG["vis"]["grasp"]["figs"]["depth_image"]:
            vis.imshow(depth_im)
            vis.title("Depth image")
            vis.show()
        if MAIN_CFG["vis"]["grasp"]["figs"]["segmask"] and segmask is not None:
            vis.imshow(segmask)
            vis.title("Segmask image")
            vis.show()

        # Aggregate color and depth images into a single
        # BerkeleyAutomation/perception `RgbdImage`.
        rgbd_im = RgbdImage.from_color_and_depth(color_im, depth_im)

        # Mask bounding box
        if bounding_box is not None:
            # Calc bb parameters.
            min_x = bounding_box.minX
            min_y = bounding_box.minY
            max_x = bounding_box.maxX
            max_y = bounding_box.maxY

            # Contain box to image->don't let it exceed image height/width
            # bounds.
            if min_x < 0:
                min_x = 0
            if min_y < 0:
                min_y = 0
            if max_x > rgbd_im.width:
                max_x = rgbd_im.width
            if max_y > rgbd_im.height:
                max_y = rgbd_im.height

            # Mask whole image
            bb_segmask_arr = np.zeros([rgbd_im.height, rgbd_im.width])
            bb_segmask_arr[min_y:max_y, min_x:max_x] = 255
            bb_segmask = BinaryImage(bb_segmask_arr.astype(np.uint8), segmask.frame)
            segmask = segmask.mask_binary(bb_segmask)

        # Visualize
        if MAIN_CFG["vis"]["grasp"]["figs"]["rgbd_state"]:
            masked_rgbd_im = rgbd_im.mask_binary(segmask)
            vis.figure()
            vis.title("Masked RGBD state")
            vis.subplot(1, 2, 1)
            vis.imshow(masked_rgbd_im.color)
            vis.subplot(1, 2, 2)
            vis.imshow(masked_rgbd_im.depth)
            vis.show()

        # Create an `RgbdImageState` with the cropped `RgbdImage` and
        # `CameraIntrinsics`.
        rgbd_state = RgbdImageState(rgbd_im, self.sensor.ir_intrinsics, segmask=segmask)

        # Execute policy
        try:
            return self.execute_policy(
                rgbd_state, self.grasping_policy, self.sensor.ir_intrinsics.frame
            )
        except NoValidGraspsException:
            mod_logger.error(
                (
                    "While executing policy found no valid grasps from sampled"
                    " antipodal point pairs. Aborting Policy!"
                )
            )

    def execute_policy(self, rgbd_image_state, grasping_policy, pose_frame):
        """Executes a grasping policy on an `RgbdImageState`.

        Parameters
        ----------
        rgbd_image_state : :py:class:`gqcnn.RgbdImageState`
            The :py:class:`gqcnn.RgbdImageState` that encapsulates the
            depth and color image along with camera intrinsics.
        grasping_policy : :py:class:`gqcnn.grasping.policy.policy.GraspingPolicy`
            Grasping policy to use.
        pose_frame : :py:obj:`str`
            Frame of reference to publish pose in.
        """

        # Execute the policy"s action
        grasp_planning_start_time = time.time()
        action = grasping_policy(rgbd_image_state)
        mod_logger.info(
            "Total grasp planning time: "
            + str(time.time() - grasp_planning_start_time)
            + " secs."
        )

        # Create `GQCNNGrasp` object and populate it
        gqcnn_grasp = GQCNNGrasp()
        gqcnn_grasp.q_value = action.q_value
        gqcnn_grasp.pose = action.grasp.pose()
        if isinstance(action.grasp, Grasp2D):
            gqcnn_grasp.grasp_type = GQCNNGrasp.PARALLEL_JAW
        elif isinstance(action.grasp, SuctionPoint2D):
            gqcnn_grasp.grasp_type = GQCNNGrasp.SUCTION
        else:
            mod_logger.error("Grasp type not supported!")

        # Store grasp representation in image space
        gqcnn_grasp.center_px[0] = action.grasp.center[0]
        gqcnn_grasp.center_px[1] = action.grasp.center[1]
        gqcnn_grasp.angle = action.grasp.angle
        gqcnn_grasp.depth = action.grasp.depth

        # Create small thumbnail and add to the grasp
        if MAIN_CFG["grasp_detection_result"]["include_thumbnail"]:
            if (
                MAIN_CFG["grasp_detection_result"]["thumbnail_resize"] < 0
                or MAIN_CFG["grasp_detection_result"]["thumbnail_resize"] > 1
            ):
                mod_logger.error(
                    "Tumnail_resize vallue is invallid. Please check your "
                    "configuration file and try again."
                )
                sys.exit(0)
            else:
                scale_factor = MAIN_CFG["grasp_detection_result"]["tumbnail_resize"]
                resized_data = imresize(
                    rgbd_image_state.rgbd_im.color.data.astype(np.float32),
                    scale_factor,
                    "bilinear",
                )
                thumbnail = ColorImage(
                    resized_data.astype(np.uint8), rgbd_image_state.rgbd_im.color._frame
                )
                gqcnn_grasp.thumbnail = thumbnail

        # Visualize result
        if MAIN_CFG["vis"]["grasp"]["figs"]["final_grasp"]:
            vis.figure(size=(10, 10))
            vis.imshow(
                rgbd_image_state.rgbd_im.color,
                vmin=self.cfg["policy"]["vis"]["vmin"],
                vmax=self.cfg["policy"]["vis"]["vmax"],
            )
            vis.grasp(action.grasp, scale=2.5, show_center=False, show_axis=True)
            vis.title(
                "Planned grasp at depth {0:.3f}m with Q={1:.3f}".format(
                    action.grasp.depth, action.q_value
                )
            )
            vis.show()
        return gqcnn_grasp
