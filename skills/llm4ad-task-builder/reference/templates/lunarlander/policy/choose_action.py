"""LunarLander control policy using heuristic rules.

This script demonstrates the RL policy pattern:
- The choose_action function is called per timestep by the evaluator
- It maps (state, last_action, previous_state) to an action
- The evaluator runs episodes internally using gymnasium

The function between EVOLVE_START and EVOLVE_END will be evolved by the LLM.
"""

import json
import sys


# EVOLVE_START
def choose_action(s, last_action, s_pre):
    """Select an action for LunarLander to achieve safe landing.

    Args:
        s: Current state [x, y, vx, vy, angle, angular_velocity, left_leg, right_leg]
           - x, y: position coordinates
           - vx, vy: velocity
           - angle: orientation angle
           - angular_velocity: rotational speed
           - left_leg, right_leg: 1 if leg touching ground, 0 otherwise
        last_action: Action taken in previous step (0-3)
        s_pre: State before last action was executed

    Returns:
        int: Action (0=nothing, 1=left engine, 2=main engine, 3=right engine)
    """
    # Heuristic control strategy
    hover_targ = 0.55  # Target hover height
    angle_targ = s[0] * 0.5 + s[2] * 1.0  # Target angle based on position and velocity
    hover_todo = (hover_targ - s[1]) * 0.5 - s[3] * 0.5  # Height correction
    angle_todo = (angle_targ - s[4]) * 0.5 - s[5] * 1.0  # Angle correction

    # Determine action based on corrections needed
    if hover_todo > abs(angle_todo) and hover_todo > 0.05:
        a = 2  # Fire main engine
    elif angle_todo > 0.05:
        a = 3  # Fire right engine (rotate left)
    elif angle_todo < -0.05:
        a = 1  # Fire left engine (rotate right)
    else:
        a = 0  # Do nothing

    return a
# EVOLVE_END


def main():
    """Main entry point for standalone testing.

    Reads JSON input: {"s": [...], "last_action": 0, "s_pre": [...]}
    Prints JSON output: {"action": 0}
    """
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No input provided"}))
        sys.exit(1)

    try:
        input_data = json.loads(sys.argv[1])
        s = input_data["s"]
        last_action = input_data["last_action"]
        s_pre = input_data["s_pre"]

        action = choose_action(s, last_action, s_pre)
        print(json.dumps({"action": action}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
