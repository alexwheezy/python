import os, re

import hou
from type_extensions import *
from nodes_color import *


position = None

def cursorPosition(network):
	global position
	position = network.cursorPosition()


def getNetworkEditor():
    editors = [pane for pane in hou.ui.paneTabs() \
    		  if isinstance(pane, hou.NetworkEditor) and pane.isCurrentTab()][-1]
    ctx = editors.pwd()
    type_ctx = ctx.type().childTypeCategory()
    return editors, ctx, type_ctx


def matchTypes(extensions):
	return re.compile(r'({})$'.format('|'.join(re.escape(x) for x in extensions)))


def baseName(filename, extensions):
	filepath, basename = os.path.split(filename)
	name = basename.split(os.extsep)[0]
	ext = matchTypes(extensions).search(filename).group() if matchTypes(extensions).search(filename) else ''
	return name, ext


def copImages(network, ctx, filename):
	if not matchTypes(IMAGE_EXTENSIONS).search(filename):
		return

	name = baseName(filename, IMAGE_EXTENSIONS)[0]
	image = ctx.createNode('file', node_name=name)
	image.setColor(hou.Color(IMAGE_NODE_COLOR))
	image.setPosition(position)
	image.parm('filename1').set(filename)


def chanFiles(network, ctx, filename):
	if not matchTypes(CHAN_EXTENSIONS).search(filename):
		return

	name = baseName(filename, CHAN_EXTENSIONS)[0]
	chan = ctx.createNode('file', node_name=name)
	chan.setColor(hou.Color(CLIP_NODE_COLOR))
	chan.setPosition(position)
	chan.parm('file').set(filename)


def shopImages(network, ctx, filename):
	if not matchTypes(IMAGE_EXTENSIONS).search(filename):
		return

	name = baseName(filename, IMAGE_EXTENSIONS)[0]
	if ctx.type().name() == 'mat' or ctx.type().name() == 'vopmaterial':
		image = ctx.createNode('texture', node_name=name)
		image.parm('map').set(filename)

	elif ctx.type().name() == 'arnold_vopnet':
		image = ctx.createNode('image', node_name=name)
		image.parm('filename').set(filename)

	image.setColor(hou.Color(IMAGE_NODE_COLOR))
	image.setPosition(position)


def objGeom(network, ctx, filename):
	if not matchTypes(GEO_EXTENSIONS).search(filename):
		return

	name, ext = baseName(filename, GEO_EXTENSIONS)

	if ext == '.fbx':
		hou.hipFile.importFBX(filename)
		return

	if ext == '.ass':
		procedural = ctx.createNode('arnold_procedural', node_name=name.title())
		procedural.setPosition(position)
		procedural.parm('ar_filename').set(filename)
		return

	geo = ctx.createNode('geo', node_name=name.title())
	geo.setPosition(position)

	for child in geo.children():
		child.destroy()

	if ext == '.abc':
		alembic = geo.createNode('alembic', node_name='Import_Alembic')
		geo.setColor(hou.Color(ALEMBIC_NODE_COLOR))
		alembic.parm('fileName').set(filename)
	else:
		geometry = geo.createNode('file', node_name='Import_Geometry')
		geo.setColor(hou.Color(GEO_NODE_COLOR))
		geometry.parm('file').set(filename)


def sopGeom(network, ctx, filename):
	if not matchTypes(GEO_EXTENSIONS).search(filename):
		return

	name, ext = baseName(filename, GEO_EXTENSIONS)

	if ext == '.ass':
		procedural = ctx.createNode('arnold_asstoc', node_name=name.title())
		procedural.setPosition(position)
		procedural.parm('ass_file').set(filename)
		return

	if ext == '.abc':
		alembic = ctx.createNode('alembic', node_name=name)
		alembic.setColor(hou.Color(ALEMBIC_NODE_COLOR))
		alembic.parm('fileName').set(filename)
	else:
		geometry = ctx.createNode('file', node_name=name)
		geometry.setColor(hou.Color(GEO_NODE_COLOR))
		geometry.parm('file').set(filename)


def hdaAsset(network, ctx, filename):
	if not matchTypes(ASSET_EXTENSIONS).search(filename):
		return

	name, ext = baseName(filename, ASSET_EXTENSIONS)
	hou.hda.installFile(filename)


def loadContents(network, ctx, type_ctx, filename):
	if type_ctx == hou.objNodeTypeCategory():
		hdaAsset(network, ctx, filename)

	if type_ctx == hou.objNodeTypeCategory():
		objGeom(network, ctx, filename)

	if type_ctx == hou.sopNodeTypeCategory():
		sopGeom(network, ctx, filename)

	if type_ctx == hou.vopNodeTypeCategory():
		shopImages(network, ctx, filename)

	if type_ctx == hou.chopNodeTypeCategory():
		chanFiles(network, ctx, filename)

	if type_ctx ==  hou.cop2NodeTypeCategory():
		copImages(network, ctx, filename)


def dropAccept(filelist):
	# Exclude hip files
    if filelist and os.path.splitext(filelist[0])[1] == ".hip":
        return False

    for filename in filelist:
    	network, ctx, type_ctx = getNetworkEditor()
    	cursorPosition(network) # The current cursor position for creating a node
    	loadContents(network, ctx, type_ctx, filename)

    return True