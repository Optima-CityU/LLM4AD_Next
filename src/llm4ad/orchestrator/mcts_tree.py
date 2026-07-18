"""Monte Carlo Tree Search structures for MCTS-AHD.

Migrated from the legacy LLM4AD ``method/mcts_ahd/mcts.py``. Provides the
tree node and the UCT-based search bookkeeping (backpropagation, UCT scoring)
used by the MCTS-AHD orchestrator.

Node ``Q`` values track algorithm scores (higher is better). The exploration
term is scaled by the remaining sample budget so the search exploits more as
the budget is consumed.
"""

from __future__ import annotations

import math
from typing import Any


class MCTSNode:
    """A node in the MCTS-AHD search tree.

    Each non-root node wraps one evolved algorithm. ``Q`` is the node's value
    (algorithm score, higher is better), ``visits`` counts UCT visits, and
    ``subtree`` collects descendants of the root's direct children for the
    e1 (root-level cross) operator.
    """

    def __init__(
        self,
        algorithm_desc: str,
        code: str,
        q: float = 0.0,
        depth: int = 0,
        individual: Any | None = None,
        parent: MCTSNode | None = None,
        visits: int = 0,
    ) -> None:
        """Initialize an MCTS node.

        Args:
            algorithm_desc: Natural-language algorithm description (or "Root").
            code: Code signature string (or "Root").
            q: Initial Q value (algorithm score).
            depth: Depth in the tree (root = 0).
            individual: The wrapped Algorithm object (None for root).
            parent: Parent node.
            visits: Initial visit count.
        """
        self.algorithm_desc = algorithm_desc
        self.code = code
        self.parent = parent
        self.depth = depth
        self.individual = individual
        self.children: list[MCTSNode] = []
        self.visits = visits
        self.subtree: list[MCTSNode] = []
        self.q = q

    def add_child(self, child: MCTSNode) -> None:
        """Append a child node.

        Args:
            child: The child node to attach.
        """
        self.children.append(child)


class MCTS:
    """UCT-based Monte Carlo tree for algorithm design.

    Tracks global Q normalization bounds and provides backpropagation and
    UCT scoring. ``alpha`` controls progressive widening; ``lambda_0`` is the
    base exploration constant.
    """

    def __init__(self, root_desc: str, alpha: float, lambda_0: float, max_depth: int = 10) -> None:
        """Initialize the tree with a root node.

        Args:
            root_desc: Placeholder description for the root ("Root").
            alpha: Progressive-widening exponent.
            lambda_0: Base exploration constant.
            max_depth: Maximum tree depth.
        """
        self.exploration_constant_0 = lambda_0
        self.alpha = alpha
        self.max_depth = max_depth
        self.epsilon = 1e-10
        self.discount_factor = 1.0
        self.q_min = 0.0
        self.q_max = -10000.0
        self.rank_list: list[float] = []
        self.root = MCTSNode(algorithm_desc=root_desc, code="Root", depth=0)

    def backpropagate(self, node: MCTSNode) -> None:
        """Backpropagate a newly evaluated node's value up to the root.

        Updates parent Q values (max-child rule), visit counts, global Q
        bounds, and the root-child subtree lists.

        Args:
            node: The freshly expanded/evaluated node.
        """
        if node.q not in self.rank_list:
            self.rank_list.append(node.q)
            self.rank_list.sort()
        self.q_min = min(self.q_min, node.q)
        self.q_max = max(self.q_max, node.q)

        parent = node.parent
        while parent is not None:
            if parent.children:
                best_child_q = max(child.q for child in parent.children)
                parent.q = parent.q * (1 - self.discount_factor) + best_child_q * self.discount_factor
            parent.visits += 1
            if parent.code != "Root" and parent.parent is not None and parent.parent.code == "Root":
                parent.subtree.append(node)
            parent = parent.parent

    def uct(self, node: MCTSNode, eval_remain: float) -> float:
        """Compute the UCT score for a node.

        Args:
            node: The candidate node.
            eval_remain: Remaining fraction of the sample budget (0..1).

        Returns:
            UCT score balancing exploitation (normalized Q) and exploration.
        """
        exploration_constant = self.exploration_constant_0 * eval_remain
        denom = (self.q_max - self.q_min) or self.epsilon
        parent_visits = node.parent.visits if node.parent else 1
        exploitation = (node.q - self.q_min) / denom
        exploration = exploration_constant * math.sqrt(
            math.log(parent_visits + 1) / (node.visits or self.epsilon)
        )
        return exploitation + exploration
