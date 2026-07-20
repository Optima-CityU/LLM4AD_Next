"""MCTS tree structures for the MCTS-AHD orchestrator.

Migrated from legacy LLM4AD MCTS-AHD method (llm4ad/method/mcts_ahd/mcts.py).
Provides the ``MCTSNode`` and ``MCTS`` classes implementing UCT selection and
backpropagation over a tree of candidate algorithms.
"""

from __future__ import annotations

import math

from llm4ad.planner.base import Algorithm


class MCTSNode:
    """MCTS tree node representing an algorithm design.

    Each node stores an algorithm, its Q-value (accumulated reward), and
    parent/children links for tree traversal.
    """

    def __init__(
        self,
        algorithm: Algorithm | None = None,
        depth: int = 0,
        parent: MCTSNode | None = None,
        is_root: bool = False,
    ):
        """Initialize an MCTS node.

        Args:
            algorithm: Algorithm individual (None for the root placeholder).
            depth: Tree depth (0 for root).
            parent: Parent node.
            is_root: Whether this is the root node.
        """
        self.algorithm = algorithm
        self.depth = depth
        self.parent = parent
        self.is_root = is_root

        self.children: list[MCTSNode] = []
        self.visits: int = 0
        self.Q: float = 0.0  # Accumulated reward (higher is better)
        self.reward: float = 0.0  # Immediate reward from evaluation
        self.subtree: list[MCTSNode] = []

    def add_child(self, child_node: MCTSNode) -> None:
        """Add a child node and set its parent to this node."""
        self.children.append(child_node)
        child_node.parent = self


class MCTS:
    """MCTS tree for algorithm-design exploration.

    Implements UCT selection and backpropagation following the legacy MCTS-AHD
    design (scores are in "higher is better" space).
    """

    def __init__(
        self,
        root_algorithm: Algorithm | None = None,
        lambda0: float = 0.1,
        alpha: float = 0.5,
        max_depth: int = 10,
    ):
        """Initialize the MCTS tree.

        Args:
            root_algorithm: Root algorithm (usually None placeholder).
            lambda0: Base exploration constant λ₀.
            alpha: Progressive-widening parameter (reserved).
            max_depth: Maximum tree depth.
        """
        self.exploration_constant_0 = lambda0
        self.alpha = alpha
        self.max_depth = max_depth

        self.discount_factor = 1.0  # Legacy uses 1.0 (no discount)
        self.q_min = 0.0
        self.q_max = -10000.0
        self.rank_list: list[float] = []

        self.root = MCTSNode(algorithm=root_algorithm, depth=0, is_root=True)

    def backpropagate(self, node: MCTSNode) -> None:
        """Backpropagate a node's reward up to the root.

        Updates Q-value bounds and, for each ancestor, sets Q to the max child
        Q (discount_factor=1) and increments the visit count.

        Args:
            node: Leaf node to backpropagate from.
        """
        if node.Q not in self.rank_list:
            self.rank_list.append(node.Q)
            self.rank_list.sort()
        self.q_min = min(self.q_min, node.Q)
        self.q_max = max(self.q_max, node.Q)

        parent = node.parent
        while parent:
            if parent.children:
                best_child_q = max(child.Q for child in parent.children)
                parent.Q = (
                    parent.Q * (1 - self.discount_factor)
                    + best_child_q * self.discount_factor
                )
            parent.visits += 1
            if not node.is_root and parent.parent and parent.parent.is_root:
                parent.subtree.append(node)
            parent = parent.parent

    def uct(self, node: MCTSNode, eval_remain: float) -> float:
        """Compute the UCT value of a node.

        UCT = (Q - Q_min)/(Q_max - Q_min) + λ·sqrt(log(parent_visits+1)/visits),
        where λ = λ₀·eval_remain.

        Args:
            node: Node to score.
            eval_remain: Remaining evaluation budget ratio in [0, 1].

        Returns:
            The UCT value (``inf`` for unvisited nodes).
        """
        if node.visits == 0:
            return float("inf")

        exploration_constant = self.exploration_constant_0 * eval_remain
        q_range = self.q_max - self.q_min
        q_normalized = 0.5 if q_range < 1e-10 else (node.Q - self.q_min) / q_range
        parent_visits = node.parent.visits if node.parent else 1
        exploration_bonus = exploration_constant * math.sqrt(
            math.log(parent_visits + 1) / node.visits
        )
        return q_normalized + exploration_bonus
