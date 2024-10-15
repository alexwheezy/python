import hou
from typing import List


def isSopNode(node: hou.OpNode) -> bool:
    """Returns True if this node is top-level and SOP level."""
    return isinstance(node, hou.SopNode) and not node.isInsideLockedHDA()


def traverseNetwork(node: hou.OpNode, nodes: List[hou.OpNode]):
    """Recursively traverses all dependencies including references,
    parameters and incoming connections."""
    if not node:
        return

    curr_node = None
    inputs = node.inputs()
    dependents = set(filter(isSopNode, (*node.dependents(), *node.references())))
    find_nodes = (*inputs, *dependents)
    for node in find_nodes:
        if node and not node in nodes:
            nodes.append(node)
            curr_node = node
            traverseNetwork(curr_node, nodes)


def dependencyNodes(node: hou.OpNode) -> List[hou.ObjNode]:
    """Returns all nodes that are dependencies of the given node."""
    nodes = []
    for node in (node, *node.inputAncestors()):
        if not node in nodes:
            nodes.append(node)
        traverseNetwork(node, nodes)
    return nodes


# Example usage
color = (0.5, 0.2, 0.20)
node = hou.selectedNodes()[0]
for node in dependencyNodes(node):
    node.setColor(hou.Color(color))

