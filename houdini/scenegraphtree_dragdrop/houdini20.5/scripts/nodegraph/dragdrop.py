"""
This script is used to customize the handling when user drags something onto
the network editor.

This script must define 3 functions that will be called by the network editor.

1. dropTest
2. dropGetChoices
3. dropAccept
"""

import hou


# Called to test whether we want to handle a drop of the source at the
# specified position.  The return value is a boolean tuple.  The first
# value indicates whether we handle the drop in the script while the
# second value indicates whether to allow the default handling to take
# place.
#
# ARGUMENTS
#    pane
#        The hou.NetworkEditor the cursor is currently over.
#    source
#        An object to query what is currently being dragged.
#    position
#        A tuple of two integers representing the pixel position
#


def dropTest(pane, source, position):
    if source.hasData(hou.qt.mimeType.usdPrimitivePath):
        return True, False
    return False, False


# Called to get the handling options to present to the user.  Returns
# (token_list, label_list, help_list), a tuple of string lists.  The
# first string list represents menu tokens, the second, menu labels,
# and the third, help for the corresponding menu entry.
#
# ARGUMENTS
#    pane
#        The hou.NetworkEditor the cursor is currently over.
#    source
#        An object to query what is currently being dragged.
#    position
#        A tuple of two integers representing the pixel position
#


def dropGetChoices(pane, source, position):
    return ["allprims"], ["All Prims"], ["All Prims"]


def itemUnderCursor(pane):
    if not pane:
        return

    rect = pane.visibleBounds()
    rect_data = pane.networkItemsInBox(
        pane.posToScreen(rect.min()),
        pane.posToScreen(rect.max()),
        for_select=True,
    )
    cursor_node = None
    if rect_data:
        cursor_pos = pane.cursorPosition()
        min_dist_to_cursor = 0.5

        for item, item_type, _ in rect_data:
            if item_type == "node":
                node_center = item.position() + item.size() / 2
                dist = node_center.distanceTo(cursor_pos)
                if dist < min_dist_to_cursor:
                    min_dist_to_cursor = dist
                    cursor_node = item

    return cursor_node


# Called to process the handling option selected by user.  Returns whether
# or not the drop was successfully processed.
#
# ARGUMENTS
#    pane
#        The hou.NetworkEditor the cursor is currently over.
#    source
#        An object to query what is currently being dragged.
#    position
#        A tuple of two integers representing the pixel position
#    token
#        The handling option selected from the dropGetChoices() list.


def dropAccept(pane, source, position, token):
    if isinstance(pane, hou.NetworkEditor) and isinstance(pane.pwd(), hou.LopNetwork):
        node = itemUnderCursor(pane)
        if node:
            primpaths = []
            data = source.data(hou.qt.mimeType.usdPrimitivePath)
            if token == "allprims":
                for primpath in data:
                    primpaths.append(primpath)
            else:
                primpaths.append(next(data))
            primpattern = node.parm("primpath") or node.parm("primpattern")
            if primpattern:
                primpattern.set(" ".join(primpaths))

    return True
