You are working on an existing ROV control system.

IMPORTANT:
The existing system already contains working features.
Your task is ONLY to implement, improve, and integrate the AUTONOMOUS CONTROL subsystem.

DO NOT modify, refactor, rename, remove, or redesign unrelated existing features.

The existing manual control, telemetry, camera streaming, API, WebSocket, MAVLink communication, dashboard/UI, joystick, logging, and other working features MUST continue working exactly as before.

==================================================
1. PRIMARY OBJECTIVE
==================================================

Implement a production-oriented autonomous mission system for an underwater ROV.

The autonomous system must be modular, fail-safe, configurable, and ready to operate once a YOLO object-detection model is provided.

The AI/object detection model must NOT be hardcoded into the autonomous logic.

The autonomous system must only depend on a detector interface.

The final user workflow should be approximately:

1. Place the trained YOLO model file in the configured model path.
2. Start the system.
3. Enable autonomous mode.
4. Autonomous controller executes the mission.

The model should be replaceable without modifying autonomous control logic.

==================================================
2. OBJECT DETECTION CLASSES
==================================================

The detector must support exactly these initial classes:

CLASS 0:
payload

CLASS 1:
gripper

Do NOT implement hook detection at this stage.

The payload is the primary mission target.

The gripper is detected visually and is used as the reference for visual alignment.

Do not create separate classes based on payload material.

For example:

payload_3dprint
payload_acrylic

MUST NOT exist.

Both are simply:

payload

==================================================
3. HIGH LEVEL AUTONOMOUS MISSION
==================================================

Implement the following finite-state-machine:

IDLE
  ↓
NAV_TO_WAYPOINT
  ↓
SEARCH
  ↓
ALIGN
  ↓
APPROACH
  ↓
GRAB
  ↓
VERIFY_GRAB
  ↓
RETURN
  ↓
DOCKING
  ↓
COMPLETE

Failure conditions must transition to an appropriate safe state.

Example:

SEARCH timeout
    ↓
FAILSAFE / ABORT

ALIGN timeout
    ↓
FAILSAFE / ABORT

APPROACH timeout
    ↓
STOP / FAILSAFE

VERIFY_GRAB failure
    ↓
RETRY or ABORT

The exact retry policy must be configurable.

==================================================
4. NAV_TO_WAYPOINT
==================================================

The waypoint phase is NOT responsible for detecting the payload.

Its purpose is only to move the ROV toward the predefined search area.

Once the ROV reaches an acceptable waypoint tolerance:

NAV_TO_WAYPOINT
    ↓
SEARCH

Waypoint coordinates, tolerance, timeout, and navigation parameters must be configurable.

Do not hardcode mission coordinates in source code.

==================================================
5. SEARCH STATE
==================================================

SEARCH is responsible for finding the payload using the vision detector.

During SEARCH:

- Read the latest camera frame.
- Run object detection.
- Look for class "payload".
- Ignore detections below configurable confidence.
- Do NOT move aggressively when the payload is not detected.
- Implement a controlled search behavior.
- Search must have a timeout.
- Search behavior must be configurable.

Example behavior:

SEARCH
 ↓
payload detected consistently
 ↓
ALIGN

If payload is not detected:

SEARCH continues.

If timeout occurs:

SEARCH_FAILED
 ↓
SAFE STOP / ABORT

Do not allow the ROV to move forward indefinitely while the target is lost.

==================================================
6. DETECTOR ARCHITECTURE
==================================================

Create a clean detector abstraction.

Example conceptual interface:

Detector
    ├── load_model()
    ├── detect(frame)
    └── get_detections()

The autonomous controller MUST NOT directly depend on YOLO-specific implementation details.

Use an adapter such as:

YOLODetector

The future model should be replaceable without modifying:

- FSM
- controller
- navigation
- gripper logic
- mission logic

Example:

YOLODetector
    ↓
DetectionResult
    ↓
Autonomous Controller

DetectionResult should contain at minimum:

class_name
confidence
x1
y1
x2
y2
center_x
center_y

==================================================
7. MODEL CONFIGURATION
==================================================

The model path must be configurable.

Example:

MODEL_PATH=/models/rov_best.pt

or through the existing project configuration system.

Do NOT hardcode:

/home/user/model.pt

Do NOT require source-code modifications to change the model.

The user should only need to:

1. place the model file
2. update configuration if necessary
3. start the system

If the project already has a configuration system, use it instead of creating a second conflicting configuration system.

==================================================
8. PAYLOAD DETECTION
==================================================

The primary target is:

class_name == "payload"

Use configurable:

CONFIDENCE_THRESHOLD

Example:

0.50

Do not hardcode the threshold inside the controller.

==================================================
9. GRIPPER DETECTION
==================================================

The second target is:

class_name == "gripper"

The gripper is used as a visual reference.

Do NOT assume the gripper is always located at the center of the image.

Do NOT hardcode a fixed gripper pixel coordinate unless explicitly configured as a fallback.

The controller should derive a gripper reference point from the detected gripper bounding box.

Example conceptual calculation:

gripper_reference_x
gripper_reference_y

The exact reference point should be configurable.

For example:

reference_x = bbox_center_x

reference_y = bbox_y1 + reference_ratio * bbox_height

The reference ratio MUST be configurable.

Do not hardcode an arbitrary ratio without documenting it.

==================================================
10. VISUAL ALIGNMENT
==================================================

The ALIGN state must calculate the relative error between:

PAYLOAD REFERENCE POINT

and

GRIPPER REFERENCE POINT

Example:

payload_center_x
payload_center_y

gripper_reference_x
gripper_reference_y

error_x =
payload_center_x - gripper_reference_x

error_y =
payload_center_y - gripper_reference_y

Interpretation:

error_x:
horizontal alignment error

error_y:
vertical alignment error

==================================================
11. ROV MOVEMENT MODEL
==================================================

The ROV does NOT have direct lateral strafing/sway control.

Therefore:

horizontal visual error must primarily be corrected using YAW.

vertical visual error must primarily be corrected using HEAVE.

Forward/backward movement is used for APPROACH.

Concept:

error_x
    ↓
YAW

error_y
    ↓
HEAVE

distance / approach condition
    ↓
FORWARD / BACKWARD

Do NOT implement lateral/sway control.

Do NOT assume the ROV can move sideways.

==================================================
12. PROPORTIONAL CONTROL
==================================================

Avoid crude fixed-duration movement such as:

yaw_right_for_2_seconds()

Instead, implement configurable proportional control.

Concept:

yaw_command =
Kp_yaw * error_x

heave_command =
Kp_heave * error_y

Commands must be clamped to configurable limits.

Example configuration:

KP_YAW
KP_HEAVE

MAX_YAW
MAX_HEAVE

Do not allow controller output to exceed configured safety limits.

==================================================
13. DEADZONE
==================================================

Implement configurable alignment deadzones.

Example:

ALIGNMENT_X_DEADZONE
ALIGNMENT_Y_DEADZONE

If:

abs(error_x) < X_DEADZONE

then:

yaw_command = 0

If:

abs(error_y) < Y_DEADZONE

then:

heave_command = 0

This prevents oscillation around the target.

==================================================
14. ALIGNMENT STABILITY
==================================================

Do NOT immediately declare alignment success from a single frame.

The target must remain within tolerance for a configurable number of consecutive frames or configurable duration.

Example:

ALIGNMENT_TOLERANCE_X
ALIGNMENT_TOLERANCE_Y
ALIGNMENT_STABLE_FRAMES

Concept:

frame 1 → aligned
frame 2 → aligned
frame 3 → aligned
frame 4 → aligned
frame 5 → aligned

Then:

ALIGNMENT_CONFIRMED

Only after confirmation may the system transition to APPROACH.

==================================================
15. TARGET LOSS HANDLING
==================================================

The controller must handle temporary detection loss.

Do NOT immediately abort because one frame has no detection.

Implement:

TARGET_LOST_TIMEOUT

Example:

payload detected
    ↓
detection lost
    ↓
short recovery period
    ↓
if target returns → continue
if timeout → SEARCH

During target loss:

- stop forward motion
- avoid uncontrolled movement
- optionally perform controlled search correction
- transition safely

==================================================
16. APPROACH STATE
==================================================

After alignment is confirmed:

ALIGN
 ↓
APPROACH

The ROV moves forward toward the payload.

Do not simply command:

FORWARD = maximum

Use a configurable approach speed.

Approach must continue to perform visual correction.

Important:

APPROACH is NOT:

ALIGN once
 ↓
FORWARD forever

Instead:

DETECT
 ↓
CORRECT
 ↓
MOVE FORWARD
 ↓
DETECT AGAIN
 ↓
CORRECT
 ↓
MOVE FORWARD

This is a closed-loop visual approach.

==================================================
17. DISTANCE / STOP CONDITION
==================================================

If no reliable depth/distance estimate exists, use a configurable visual proxy such as payload bounding-box size.

Example:

payload_bbox_width

As the ROV approaches:

payload_bbox_width increases.

Define:

APPROACH_TARGET_SIZE

When the target reaches the configured visual size:

APPROACH
 ↓
STOP
 ↓
GRAB

Do NOT claim that bbox size is an exact physical distance.

Treat it as a configurable visual proximity estimate.

==================================================
18. GRAB STATE
==================================================

Once the ROV is sufficiently aligned and close:

1. Stop translational movement.
2. Stabilize.
3. Activate gripper.
4. Wait for configured gripper action time.
5. Transition to VERIFY_GRAB.

Gripper control must use the existing gripper interface if one already exists.

DO NOT replace existing gripper implementation unless absolutely necessary.

==================================================
19. VERIFY_GRAB
==================================================

The system must not assume that the payload was successfully grabbed.

Implement a verification phase.

Possible visual verification:

- payload remains associated with gripper
- payload position moves together with gripper
- payload is no longer left at the original position

Verification logic must be modular.

Create an interface/function that can later be improved without rewriting the FSM.

Example:

verify_grab()

returns:

SUCCESS
FAILURE
UNKNOWN

The behavior for UNKNOWN must be configurable.

==================================================
20. RETURN
==================================================

After successful verification:

VERIFY_GRAB
    ↓
RETURN

Return navigation must use the existing navigation/MAVLink implementation where possible.

Do NOT implement a second independent MAVLink navigation system.

Reuse existing infrastructure.

==================================================
21. DOCKING
==================================================

Docking must remain modular.

Do NOT implement hook detection.

If the existing project already has docking logic or a docking marker/waypoint system, integrate with it without modifying unrelated behavior.

If docking implementation is incomplete, create a clean interface/state boundary so docking can be implemented later.

Do NOT block payload acquisition development on docking.

==================================================
22. FAILSAFE
==================================================

Autonomous control must be conservative.

At minimum handle:

- detector unavailable
- camera unavailable
- model loading failure
- target lost
- navigation timeout
- alignment timeout
- approach timeout
- gripper failure
- verification failure
- MAVLink communication failure
- unexpected state

When a critical failure occurs:

STOP autonomous movement
 ↓
enter safe state
 ↓
log reason

Never continue forward blindly after losing the target.

==================================================
23. MANUAL OVERRIDE
==================================================

Manual control must ALWAYS have priority.

If the user disables autonomous mode or requests manual control:

AUTONOMOUS CONTROL MUST IMMEDIATELY STOP issuing movement commands.

Do not break or modify the existing manual control mechanism.

Autonomous mode must never lock the operator out.

==================================================
24. COMMAND OWNERSHIP
==================================================

There must be a clear concept of command ownership.

Example:

MANUAL
AUTONOMOUS

Only the active control source may issue movement commands.

When switching:

MANUAL → AUTONOMOUS

initialize autonomous state safely.

When switching:

AUTONOMOUS → MANUAL

immediately stop autonomous commands and return control to manual subsystem.

Do not allow simultaneous conflicting commands from manual and autonomous controllers.

==================================================
25. STATE MACHINE IMPLEMENTATION
==================================================

Use explicit states.

Example:

IDLE
NAV_TO_WAYPOINT
SEARCH
ALIGN
APPROACH
GRAB
VERIFY_GRAB
RETURN
DOCKING
COMPLETE
FAILSAFE

Each state must have:

- entry behavior
- update behavior
- exit behavior
- timeout
- transition conditions

Avoid deeply nested if/else logic.

Do not create a giant autonomous function.

==================================================
26. CONFIGURATION
==================================================

All tunable parameters must be configurable.

At minimum:

MODEL_PATH

CONFIDENCE_THRESHOLD

KP_YAW
KP_HEAVE

MAX_YAW
MAX_HEAVE

APPROACH_SPEED

ALIGNMENT_X_DEADZONE
ALIGNMENT_Y_DEADZONE

ALIGNMENT_TOLERANCE_X
ALIGNMENT_TOLERANCE_Y

ALIGNMENT_STABLE_FRAMES

TARGET_LOST_TIMEOUT

SEARCH_TIMEOUT

ALIGN_TIMEOUT

APPROACH_TIMEOUT

GRAB_DURATION

VERIFY_TIMEOUT

APPROACH_TARGET_SIZE

GRIPPER_REFERENCE_RATIO

Do not scatter magic numbers throughout source code.

==================================================
27. LOGGING
==================================================

Autonomous operation must produce useful logs.

Log at minimum:

state transitions
detector status
payload confidence
gripper confidence
payload center
gripper reference point
error_x
error_y
yaw command
heave command
forward command
target lost events
timeouts
gripper action
verification result
failsafe reason

Avoid logging every frame at excessive frequency.

Use throttled/debug logging where appropriate.

==================================================
28. PERFORMANCE
==================================================

This system will run on Raspberry Pi 5.

Therefore:

- avoid unnecessary image copies
- avoid processing more frames than necessary
- use configurable inference resolution
- avoid blocking the main control loop
- separate camera acquisition from inference where appropriate
- do not allow object detection to block critical control/communication tasks

The autonomous controller must remain responsive.

==================================================
29. THREADING / PROCESSING
==================================================

If the existing architecture already uses multiprocessing, queues, async tasks, or worker processes, reuse that architecture.

Do NOT rewrite the entire application.

The preferred conceptual architecture is:

Camera
  ↓
Frame Queue / latest-frame buffer
  ↓
Detector Worker
  ↓
Detection Result
  ↓
Autonomous Controller
  ↓
Existing MAVLink / vehicle control layer

The controller should operate on the latest available detection result.

It should not accumulate stale frames.

==================================================
30. EXISTING CODE PROTECTION
==================================================

THIS IS CRITICAL.

Before modifying anything:

1. Inspect the existing project structure.
2. Identify the existing autonomous-related code.
3. Identify existing APIs.
4. Identify existing WebSocket behavior.
5. Identify existing camera streams.
6. Identify existing MAVLink implementation.
7. Identify existing joystick/manual control.
8. Identify existing gripper control.
9. Identify existing telemetry.

DO NOT modify unrelated systems.

Do not:

- rename existing endpoints
- rename existing WebSocket events
- change API response formats
- change camera streaming behavior
- change manual joystick behavior
- change MAVLink connection architecture
- change telemetry format
- remove existing features
- perform unrelated refactoring
- change frontend UI unless explicitly required for autonomous control

If an existing module must be touched, make the smallest possible change.

==================================================
31. BACKWARD COMPATIBILITY
==================================================

All existing features must behave exactly as before after autonomous implementation.

Before finishing:

Test:

MANUAL CONTROL
CAMERA STREAM
TELEMETRY
MAVLINK
GRIPPER
EXISTING API
EXISTING WEBSOCKET
EXISTING DASHBOARD

No regression is acceptable.

==================================================
32. MODEL REPLACEMENT
==================================================

The trained model must be replaceable without changing controller code.

Example:

models/
    rov_payload_gripper.pt

Configuration:

MODEL_PATH=models/rov_payload_gripper.pt

If a newer model is trained:

models/
    rov_payload_gripper_v2.pt

Only configuration changes.

The autonomous logic must remain unchanged.

==================================================
33. OUTPUT CONTRACT
==================================================

Create a stable detection result structure.

Example:

Detection:
{
    "class_id": 0,
    "class_name": "payload",
    "confidence": 0.92,
    "bbox": {
        "x1": 100,
        "y1": 120,
        "x2": 180,
        "y2": 210
    },
    "center": {
        "x": 140,
        "y": 165
    }
}

Multiple detections must be supported.

The controller should select the most appropriate target according to configurable logic.

==================================================
34. TARGET SELECTION
==================================================

If multiple payload detections exist:

Do NOT blindly use the first detection.

Implement a deterministic target-selection strategy.

Possible factors:

- confidence
- bbox size
- distance from current gripper reference
- temporal consistency

Keep this logic modular and configurable.

==================================================
35. TESTING REQUIREMENTS
==================================================

Create tests where practical.

At minimum test:

1. detector unavailable
2. no payload
3. payload detected
4. gripper detected
5. alignment error positive
6. alignment error negative
7. alignment deadzone
8. stable alignment
9. target loss
10. approach timeout
11. grab success
12. grab failure
13. manual override
14. autonomous disable
15. failsafe transition

Do not require physical hardware for every unit test.

Use mock detector and mock vehicle control where possible.

==================================================
36. DEVELOPMENT STRATEGY
==================================================

Implement incrementally.

PHASE 1:
Detector interface + YOLO adapter.

PHASE 2:
Detection result processing.

PHASE 3:
Payload/gripper reference calculation.

PHASE 4:
Visual alignment controller.

PHASE 5:
Approach controller.

PHASE 6:
Grab + verification.

PHASE 7:
FSM.

PHASE 8:
Integration with existing navigation.

PHASE 9:
Failsafe.

PHASE 10:
Testing.

Do not implement everything in one giant change.

==================================================
37. IMPORTANT IMPLEMENTATION PRINCIPLE
==================================================

The autonomous subsystem must be treated as an ADDITIVE module.

Conceptually:

EXISTING SYSTEM
    +
AUTONOMOUS SUBSYSTEM
    =
FINAL SYSTEM

NOT:

EXISTING SYSTEM
    →
REWRITTEN SYSTEM

The goal is to add autonomous capability without destabilizing existing functionality.

==================================================
38. CODE QUALITY
==================================================

Code must be:

- modular
- readable
- typed where practical
- documented
- configurable
- testable
- production-oriented

Avoid unnecessary abstractions.

Avoid overengineering.

The implementation must be practical for deployment on Raspberry Pi 5.

==================================================
39. FINAL DELIVERABLE
==================================================

Provide:

1. Complete autonomous module.
2. Detector abstraction.
3. YOLO detector implementation.
4. Configuration.
5. FSM.
6. Visual alignment controller.
7. Approach controller.
8. Grab logic integration.
9. Verification interface.
10. Failsafe handling.
11. Logging.
12. Unit/mock tests where practical.
13. Example configuration.
14. Clear instructions for placing the trained YOLO model.
15. Clear instructions for starting autonomous mode.
16. Documentation explaining the data flow.

The final implementation must be ready to run after the trained model is placed/configured.

==================================================
40. BEFORE MODIFYING CODE
==================================================

FIRST inspect the existing repository.

Do NOT immediately start coding.

Identify:

- project structure
- current entry point
- current camera pipeline
- current MAVLink layer
- current vehicle control API
- current gripper API
- current telemetry
- current manual control
- current frontend/backend boundaries

Then propose the minimal files that need to be added/modified.

ONLY after understanding the existing architecture should implementation begin.

==================================================
41. FINAL RULE
==================================================

If a requested autonomous feature conflicts with an existing working feature:

DO NOT silently modify the existing feature.

Instead:

1. preserve existing behavior
2. isolate the autonomous functionality
3. explain the conflict
4. implement the smallest compatible integration

The autonomous subsystem must NEVER break existing manual operation.

The system must always have a safe manual fallback.