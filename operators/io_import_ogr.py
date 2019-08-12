# -*- coding:utf-8 -*-
# kate: tab-indents: on; indent-width: 4; indent-mode: python; remove-trailing-spaces: all;

import os, sys, time
import bpy
from bpy.props import StringProperty, BoolProperty, EnumProperty, IntProperty
from bpy.types import Operator
import bmesh
import math
from mathutils import Vector

try:
    from osgeo import ogr
except:
    sys.exit('ERROR: cannot find GDAL/OGR modules')


from ..geoscene import GeoScene, georefManagerLayout
from ..prefs import PredefCRS
from ..core import BBOX
from ..core.proj import Reproj

from .utils import adjust3Dview, getBBOX, DropToGround

import pprint

PKG, SUBPKG = __package__.split('.', maxsplit=1)

version=280

#
featureType = {
	ogr.wkbLineString,
	ogr.wkbPoint,
	ogr.wkbPolygon
}

class IMPORT_OGR_FILE_DIALOG(Operator):
	"""Select shp file, loads the fields and start importgis.shapefile_props_dialog operator"""

	bl_idname = "importgis.ogr_file_dialog"
	bl_description = 'Import OGR support file (.*)'
	bl_label = "Import OGR"
	bl_options = {'INTERNAL'}

	# Import dialog properties
	filepath = StringProperty(
		name="File Path",
		description="Filepath used for importing the file",
		maxlen=1024,
		subtype='FILE_PATH' )

	filename_ext = ".*"

	filter_glob = StringProperty(
			default = "*.*",
			options = {'HIDDEN'} )

	def invoke(self, context, event):
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}

	def draw(self, context):
		layout = self.layout
		layout.label(text="Options will be available")
		layout.label(text="after selecting a file")

	def execute(self, context):
		if os.path.exists(self.filepath):
			bpy.ops.importgis.ogr_props_dialog('INVOKE_DEFAULT', filepath=self.filepath)
		else:
			self.report({'ERROR'}, "Invalid file")
		return{'FINISHED'}


class IMPORT_OGR_PROPS_DIALOG(Operator):
	"""Shapefile importer properties dialog"""

	bl_idname = "importgis.ogr_props_dialog"
	bl_description = 'Import OGR supported file (.*)'
	bl_label = "Import OGR"
	bl_options = {"INTERNAL"}

	filepath = StringProperty()
	has_layers = False;

	#special function to auto redraw an operator popup called through invoke_props_dialog
	def check(self, context):
		return True

	def listDriver(self, context):
		formatsList = []
		cnt = ogr.GetDriverCount()
		for i in range(cnt):
			driver = ogr.GetDriver(i)
			name = driver.GetName()
			description = driver.GetDescription()
			if not name in formatsList:
				formatsList.append( (name, description, '') )
		formatsList.sort()
		return formatsList

	def listLayer(self, context):
		layerItems = []
		has_layers = False
		if not self.driverName:
			return layerItems
		try:
			driver = ogr.GetDriverByName(self.driverName)
			dataSource = driver.Open(self.filepath, 0)
		except Exception as e:
			print("Warning : unable to read OGR file {}".format(e))
			return layerItems
		try:
			for name in dataSource:
				layerItems.append( (name, name, '') )
			has_layers = True
			return layerItems
		except Exception as e:
			print("no layers found")
			return layerItems

	def updateLayers(self, context):
		print("updateLayers", self.driverName)

	def listFields(self, context):
		fieldsItems = []
		try:
			driver = ogr.GetDriverByName(self.driverName)
			dataSource = driver.Open(self.filepath, 0)
		except Exception as e:
			print("Warning : unable to read OGR file {}".format(e))
			return fieldsItems
		print('Opened %s' % (self.filepath))
		layer = dataSource.GetLayer(1)
		layerDefinition = layer.GetLayerDefn()
		print("Number of features in %s: %d" % (os.path.basename(self.filepath),featureCount))
		for i in range(layerDefinition.GetFieldCount()):
			name = layerDefinition.GetFieldDefn(i).GetName()
			fieldsItems.append( (name. name, '') )
		return fieldsItems

	## Shapefile CRS definition
	def listPredefCRS(self, context):
		return PredefCRS.getEnumItems()

	def listObjects(self, context):
		objs = []
		for index, object in enumerate(bpy.context.scene.objects):
			if object.type == 'MESH':
				#put each object in a tuple (key, label, tooltip) and add this to the objects list
				objs.append((str(index), object.name, "Object named " +object.name))
		return objs

	reprojection = BoolProperty(
			name="Specifiy OGR CRS",
			description="Specifiy OGR CRS if it's different from scene CRS",
			default=False )

	ogrCRS = EnumProperty(
		name = "Gausz OGR CRS",
		description = "Choose a Coordinate Reference System",
		items = listPredefCRS)

	driverName = EnumProperty(
		name = "Driver",
		description = "Choose OGR driver",
		items = listDriver,
		update = updateLayers )

	layerName = EnumProperty(
		name = "Layers",
		description = "Choose layer",
		items = listLayer )

	# Elevation source
	vertsElevSource = EnumProperty(
			name="Elevation source",
			description="Select the source of vertices z value",
			items=[
			('NONE', 'None', "Flat geometry"),
			('GEOM', 'Geometry', "Use z value from shape geometry if exists"),
			('FIELD', 'Field', "Extract z elevation value from an attribute field"),
			('OBJ', 'Object', "Get z elevation value from an existing ground mesh")
			],
			default='GEOM')

	# Elevation object
	objElevLst = EnumProperty(
		name="Elev. object",
		description="Choose the mesh from which extract z elevation",
		items=listObjects )

	# Elevation field
	'''
	useFieldElev = BoolProperty(
			name="Elevation from field",
			description="Extract z elevation value from an attribute field",
			default=False )
	'''
	fieldElevName = EnumProperty(
		name = "Elev. field",
		description = "Choose field",
		items = listFields )

	#Extrusion field
	useFieldExtrude = BoolProperty(
			name="Extrusion from field",
			description="Extract z extrusion value from an attribute field",
			default=False )
	fieldExtrudeName = EnumProperty(
		name = "Field",
		description = "Choose field",
		items = listFields )

	#Extrusion axis
	extrusionAxis = EnumProperty(
			name="Extrude along",
			description="Select extrusion axis",
			items=[ ('Z', 'z axis', "Extrude along Z axis"),
			('NORMAL', 'Normal', "Extrude along normal")] )

	#Create separate objects
	separateObjects = BoolProperty(
			name="Separate objects",
			description="Import to separate objects instead one large object",
			default=False )

	#Name objects from field
	useFieldName = BoolProperty(
			name="Object name from field",
			description="Extract name for created objects from an attribute field",
			default=False )

	fieldObjName = EnumProperty(
		name = "Field",
		description = "Choose field",
		items = listFields )


	def draw(self, context):
		#Function used by blender to draw the panel.
		scn = context.scene
		layout = self.layout
		#
		layout.prop(self, 'driverName')
		#
		if self.has_layers:
			layout.prop(self, 'layerName')
		#
		layout.prop(self, 'vertsElevSource')
		#
		#layout.prop(self, 'useFieldElev')
		if self.vertsElevSource == 'FIELD':
			layout.prop(self, 'fieldElevName')
		elif self.vertsElevSource == 'OBJ':
			layout.prop(self, 'objElevLst')
		#
		layout.prop(self, 'useFieldExtrude')
		if self.useFieldExtrude:
			layout.prop(self, 'fieldExtrudeName')
			layout.prop(self, 'extrusionAxis')
		#
		layout.prop(self, 'separateObjects')
		if self.separateObjects:
			layout.prop(self, 'useFieldName')
		else:
			self.useFieldName = False
		if self.separateObjects and self.useFieldName:
			layout.prop(self, 'fieldObjName')
		#
		geoscn = GeoScene()
		#geoscnPrefs = context.user_preferences.addons['geoscene'].preferences
		if geoscn.isPartiallyGeoref:
			layout.prop(self, 'reprojection')
			if self.reprojection:
				self.ogrCRSInputLayout(context)
			#
			georefManagerLayout(self, context)
		else:
			self.ogrCRSInputLayout(context)


	def ogrCRSInputLayout(self, context):
		layout = self.layout
		row = layout.row(align=True)
		#row.prop(self, "ogrCRS", text='CRS')
		split = row.split(factor=0.35, align=True)
		split.label(text='CRS:')
		split.prop(self, "ogrCRS", text='')
		row.operator("bgis.add_predef_crs", text='', icon='ADD')


	def invoke(self, context, event):
		return context.window_manager.invoke_props_dialog(self)

	def execute(self, context):
		##elevField = self.fieldElevName if self.useFieldElev else ""
		elevField = self.fieldElevName if self.vertsElevSource == 'FIELD' else ""
		extrudField = self.fieldExtrudeName if self.useFieldExtrude else ""
		nameField = self.fieldObjName if self.useFieldName else ""
		if self.vertsElevSource == 'OBJ':
			if not self.objElevLst:
				self.report({'ERROR'}, "No elevation object")
				return {'CANCELLED'}
			else:
				objElevIdx = int(self.objElevLst)
		else:
			objElevIdx = 0 #will not be used

		geoscn = GeoScene()
		if geoscn.isBroken:
				self.report({'ERROR'}, "Scene georef is broken, please fix it beforehand")
				return {'CANCELLED'}

		if geoscn.isGeoref:
			if self.reprojection:
				ogrCRS = self.ogrCRS
			else:
				ogrCRS = geoscn.crs
		else:
			ogrCRS = self.ogrCRS

		try:
			bpy.ops.importgis.ogr('INVOKE_DEFAULT', filepath=self.filepath, ogrCRS=ogrCRS, driverName=self.driverName, layerName=self.layerName,
				elevSource=self.vertsElevSource, fieldElevName=elevField, objElevIdx=objElevIdx, fieldExtrudeName=extrudField,
				fieldObjName=nameField, extrusionAxis=self.extrusionAxis, separateObjects=self.separateObjects)
		except Exception as e:
			self.report({'ERROR'}, str(e))
			return {'CANCELLED'}

		return{'FINISHED'}


class IMPORT_OGR(Operator):
	"""Import from Gausz OGR file format (.ogr)"""

	bl_idname = "importgis.ogr" # important since its how bpy.ops.import.ogr is constructed (allows calling operator from python console or another script)
	#bl_idname rules: must contain one '.' (dot) charactere, no capital letters, no reserved words (like 'import')
	bl_description = 'Import OGR support files (.*)'
	bl_label = "Import OGR supported file"
	bl_options = {"UNDO"}

	filepath = StringProperty()

	ogrCRS = StringProperty(name = "OGR CRS", description = "Coordinate Reference System")
	driverName = StringProperty(name = "OGR driver", description = "OGR Driver")
	layerName = StringProperty(name = "OGR layer", description = "Layer")

	elevSource = StringProperty(name = "Elevation source", description = "Elevation source", default='GEOM') # [NONE, GEOM, OBJ, FIELD]
	objElevIdx = IntProperty(name = "Elevation object index", description = "")

	fieldElevName = StringProperty(name = "Elevation field", description = "Field name")
	fieldExtrudeName = StringProperty(name = "Extrusion field", description = "Field name")
	fieldObjName = StringProperty(name = "Objects names field", description = "Field name")

	#Extrusion axis
	extrusionAxis = EnumProperty(
			name="Extrude along",
			description="Select extrusion axis",
			items=[ ('Z', 'z axis', "Extrude along Z axis"),
			('NORMAL', 'Normal', "Extrude along normal")]
			)
	#Create separate objects
	separateObjects = BoolProperty(
			name="Separate objects",
			description="Import to separate objects instead one large object",
			default=False
			)

	@classmethod
	def poll(cls, context):
		return context.mode == 'OBJECT'

	def execute(self, context):

		prefs = bpy.context.preferences.addons[PKG].preferences
		# 2.79
		#prefs = bpy.context.user_preferences.addons[PKG].preferences

		#Set cursor representation to 'loading' icon
		w = context.window
		w.cursor_set('WAIT')
		t0 = time.clock()

		bpy.ops.object.select_all(action='DESELECT')

		#Path
		ogrName = os.path.basename(self.filepath)[:-4]

		#Get ogr reader
		#print("Read OGR file...")
		try:
			driver = ogr.GetDriverByName(self.driverName)
			dataSource = driver.Open(self.filepath, 0)
		except Exception as e:
			self.report({'ERROR'}, "Unable to read OGR file: " + str(e))
			return {'CANCELLED'}

		if self.layerName:
			ogrlayer = dataSource.GetLayer(self.layerName)
		else:
			ogrlayer = dataSource.GetLayer(0)
		#Check type
		ogrType = ogrlayer.GetGeomType()
		print('Feature type : ', ogrType)
		if ogrType not in featureType:
			self.report({'ERROR'}, "Cannot process %s feature type" % ogrType)
			return {'CANCELLED'}

		if self.elevSource != 'FIELD':
			self.fieldElevName = ''

		if self.elevSource == 'OBJ':
			scn = bpy.context.scene
			elevObj = scn.objects[self.objElevIdx]
			rayCaster = DropToGround(scn, elevObj)

		layerDefinition = ogrlayer.GetLayerDefn()
		fieldsNames = []
		for i in range(layerDefinition.GetFieldCount()):
			name = layerDefinition.GetFieldDefn(i).GetName()
			fieldsNames.append(name)
		#print("OGR file fields : "+str(fieldsNames))

		if self.separateObjects or self.fieldElevName or self.fieldObjName or self.fieldExtrudeName:
			self.useDbf = True
		else:
			self.useDbf = False

		if self.fieldObjName and self.separateObjects:
			try:
				nameFieldIdx = fieldsNames.index(self.fieldObjName)
			except Exception as e:
				self.report({'ERROR'}, "Unable to find name field. " + str(e))
				return {'CANCELLED'}

		if self.fieldElevName:
			try:
				zFieldIdx = fieldsNames.index(self.fieldElevName)
			except Exception as e:
				self.report({'ERROR'}, "Unable to find elevation field. " + str(e))
				return {'CANCELLED'}

			if fields[zFieldIdx][1] not in ['N', 'F', 'L'] :
				self.report({'ERROR'}, "Elevation field do not contains numeric values")
				return {'CANCELLED'}

		if self.fieldExtrudeName:
			try:
				extrudeFieldIdx = fieldsNames.index(self.fieldExtrudeName)
			except ValueError:
				self.report({'ERROR'}, "Unable to find extrusion field")
				return {'CANCELLED'}

			if fields[extrudeFieldIdx][1] not in ['N', 'F', 'L'] :
				self.report({'ERROR'}, "Extrusion field do not contains numeric values")
				return {'CANCELLED'}

		#Get shp and scene georef infos
		ogrCRS = self.ogrCRS
		geoscn = GeoScene()
		if geoscn.isBroken:
			self.report({'ERROR'}, "Scene georef is broken, please fix it beforehand")
			return {'CANCELLED'}

		scale = geoscn.scale #TODO

		if not geoscn.hasCRS: #if not geoscn.isGeoref:
			try:
				geoscn.crs = ogrCRS
			except Exception as e:
				self.report({'ERROR'}, str(e))
				return {'CANCELLED'}

		#Init reprojector class
		if geoscn.crs != ogrCRS:
			print("Data will be reprojected from " + ogrCRS + " to " + geoscn.crs)
			try:
				rprj = Reproj(ogrCRS, geoscn.crs)
			except Exception as e:
				self.report({'ERROR'}, "Unable to reproject data. " + str(e))
				return {'CANCELLED'}
			#if rprj.iproj == 'EPSGIO':
			#	if shp.numRecords > 100:
			#		self.report({'ERROR'}, "Reprojection through online epsg.io engine is limited to 100 features. \nPlease install GDAL or pyproj module.")
			#		return {'CANCELLED'}

		#Get bbox
		bbox = BBOX(ogrlayer.GetExtent())
		if geoscn.crs != ogrCRS:
			bbox = rprj.bbox(bbox)

		#Get or set georef dx, dy
		if not geoscn.isGeoref:
			dx, dy = bbox.center
			geoscn.setOriginPrj(dx, dy)
		else:
			dx, dy = geoscn.getOriginPrj()

		nbFeats = ogrlayer.GetFeatureCount()

		#Create an empty BMesh
		bm = bmesh.new()
		#Extrusion is exponentially slow with large bmesh
		#it's fastest to extrude a small bmesh and then join it to a final large bmesh
		if not self.separateObjects and self.fieldExtrudeName:
			finalBm = bmesh.new()

		if self.separateObjects and version == 280:
			layer = bpy.data.collections.new(ogrName)
			context.scene.collection.children.link(layer)

		progress = -1
		i = 0
		for feature in ogrlayer:
			#Progress infos
			pourcent = round(((i+1)*100)/nbFeats)
			if pourcent in list(range(0, 110, 10)) and pourcent != progress:
				progress = pourcent
				if pourcent == 100:
					print(str(pourcent)+'%')
				else:
					print(str(pourcent), end="%, ")
				sys.stdout.flush() #we need to flush or it won't print anything until after the loop has finished

			geom = feature.GetGeometryRef()

			#Reproj geom
			if geoscn.crs != ogrCRS:
				pts = rprj.pts(pts)

			#Get extrusion offset
			if self.fieldExtrudeName:
				try:
					offset = float(record[extrudeFieldIdx])
				except Exception as e:
					print('Warning feature {} : cannot extract extrusion value. Error {}'.format(i, e))
					offset = 0 #null values will be set to zero

			#Iter over parts
			for j in range(0, geom.GetGeometryCount()):
				# EXTRACT 3D GEOM

				#Get list of points
				g = geom.GetGeometryRef(i)

				pts = geom.getPoints()
				nbPts = len(pts)

				#Skip null geom
				if nbPts == 0:
					continue #go to next iteration of the loop

				#Build 3d geom
				pts = []
				for k in range(0, g.GetPointCount()):
					pt = g.GetPoint(k)

					#if self.elevSource == 'OBJ':
					#	rcHit = rayCaster.rayCast(x=pt[0]-dx, y=pt[1]-dy)
					#	z = rcHit.loc.z #will be automatically set to zero if not rcHit.hit
                    #
					#elif self.elevSource == 'FIELD':
					#	try:
					#		z = float(record[zFieldIdx])
					#	except Exception as e:
					#		print('Warning feature {}: cannot extract elevation value. Error {}'.format(i, e))
					#		z = 0 #null values will be set to zero
                    #
					#elif shpType[-1] == 'Z' and self.elevSource == 'GEOM':
					#	z = shape.z[idx1:idx2][k]
					#else:
					z = 0

					pts.append((pt[0], pt[1], z))

				# BUILD BMESH

				# POINTS
				if (ogrType == ogr.wkbPoint):
					vert = [bm.verts.new(pt) for pt in pts]
					#Extrusion
					if self.fieldExtrudeName and offset > 0:
						vect = (0, 0, offset) #along Z
						result = bmesh.ops.extrude_vert_indiv(bm, verts=vert)
						verts = result['verts']
						bmesh.ops.translate(bm, verts=verts, vec=vect)

				# LINES
				if (ogrType == ogr.wkbLineString):
					#Split polyline to lines
					n = len(pts)
					lines = [ (pts[i], pts[i+1]) for i in range(n) if i < n-1 ]
					#Build edges
					edges = []
					for line in lines:
						verts = [bm.verts.new(pt) for pt in line]
						edge = bm.edges.new(verts)
						edges.append(edge)
					#Extrusion
					if self.fieldExtrudeName and offset > 0:
						vect = (0, 0, offset) # along Z
						result = bmesh.ops.extrude_edge_only(bm, edges=edges)
						verts = [elem for elem in result['geom'] if isinstance(elem, bmesh.types.BMVert)]
						bmesh.ops.translate(bm, verts=verts, vec=vect)

				# NGONS
				if (ogrType == ogr.wkbPolygon):
					#According to the shapefile spec, polygons points are clockwise and polygon holes are counterclockwise
					#in Blender face is up if points are in anticlockwise order
					pts.reverse() #face up
					pts.pop() #exlude last point because it's the same as first pt
					if len(geom) >= 3: #needs 3 points to get a valid face
						verts = [bm.verts.new(pt) for pt in pts]
						face = bm.faces.new(verts)
						#update normal to avoid null vector
						face.normal_update()
						if face.normal.z < 0: #this is a polygon hole, bmesh cannot handle polygon hole
							pass #TODO
						#Extrusion
						if self.fieldExtrudeName and offset > 0:
							#build translate vector
							if self.extrusionAxis == 'NORMAL':
								normal = face.normal
								vect = normal * offset
							elif self.extrusionAxis == 'Z':
								vect = (0, 0, offset)
							faces = bmesh.ops.extrude_discrete_faces(bm, faces=[face]) #return {'faces': [BMFace]}
							verts = faces['faces'][0].verts
							if self.elevSource == 'OBJ':
								# Making flat roof (TODO add an user input parameter to setup this behaviour)
								z = max([v.co.z for v in verts]) + offset #get max z coord
								for v in verts:
									v.co.z = z
							else:
								##result = bmesh.ops.extrude_face_region(bm, geom=[face]) #return dict {"geom":[BMVert, BMEdge, BMFace]}
								##verts = [elem for elem in result['geom'] if isinstance(elem, bmesh.types.BMVert)] #geom type filter
								bmesh.ops.translate(bm, verts=verts, vec=vect)


			if self.separateObjects:

				if self.fieldObjName:
					try:
						name = record[nameFieldIdx]
					except Exception as e:
						print('Warning feature {}: cannot extract name value. Error {}'.format(i, e))
						name = ''
					# null values will return a bytes object containing a blank string of length equal to fields length definition
					if isinstance(name, bytes):
						name = ''
					else:
						name = str(name)
				else:
					name = ogrName

				#Calc bmesh bbox
				_bbox = getBBOX.fromBmesh(bm)

				#Calc bmesh geometry origin and translate coords according to it
				#then object location will be set to initial bmesh origin
				#its a work around to bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')
				ox, oy, oz = _bbox.center
				oz = _bbox.zmin
				bmesh.ops.translate(bm, verts=bm.verts, vec=(-ox, -oy, -oz))

				#Create new mesh from bmesh
				mesh = bpy.data.meshes.new(name)
				bm.to_mesh(mesh)
				bm.clear()

				#Validate new mesh
				mesh.validate(verbose=False)

				#Place obj
				obj = bpy.data.objects.new(name, mesh)
				if version == 280:
					layer.objects.link(obj)
					context.view_layer.objects.active = obj
					obj.select_set(True)
				else:
					context.scene.objects.link(obj)
					context.scene.objects.active = obj
					obj.select = True
				obj.location = (ox, oy, oz)

				# bpy operators can be very cumbersome when scene contains lot of objects
				# because it cause implicit scene updates calls
				# so we must avoid using operators when created many objects with the 'separate objects' option)
				##bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')

				#write attributes data
				print("TODO attributes data")
				#for i, field in enumerate(shp.fields):
				#	fieldName, fieldType, fieldLength, fieldDecLength = field
				#	if fieldName != 'DeletionFlag':
				#		if fieldType in ('N', 'F'):
				#			obj[fieldName] = float(record[i-1]) #cast to float to avoid overflow error when affecting custom property
				#		else:
				#			obj[fieldName] = record[i-1]

			elif self.fieldExtrudeName:
				#Join to final bmesh (use from_mesh method hack)
				buff = bpy.data.meshes.new(".temp")
				bm.to_mesh(buff)
				finalBm.from_mesh(buff)
				bpy.data.meshes.remove(buff)
				bm.clear()

		#Write back the whole mesh
		if not self.separateObjects:

			mesh = bpy.data.meshes.new(ogrName)

			if self.fieldExtrudeName:
				bm.free()
				bm = finalBm

			if prefs.mergeDoubles:
				bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
			bm.to_mesh(mesh)

			#Finish
			#mesh.update(calc_edges=True)
			mesh.validate(verbose=False) #return true if the mesh has been corrected
			obj = bpy.data.objects.new(ogrName, mesh)
			if version == 280:
				context.scene.collection.objects.link(obj)
				context.view_layer.objects.active = obj
				obj.select_set(True)
			else:
				context.scene.objects.link(obj)
				context.scene.objects.active = obj
				obj.select = True
			bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')

		#free the bmesh
		bm.free()

		t = time.clock() - t0
		print('Build in %f seconds' % t)

		#Adjust grid size
		if prefs.adjust3Dview:
			bbox.shift(-dx, -dy) #convert shapefile bbox in 3d view space
			adjust3Dview(context, bbox)


		return {'FINISHED'}

classes = [
    IMPORT_OGR_FILE_DIALOG,
    IMPORT_OGR_PROPS_DIALOG,
	IMPORT_OGR
]

def register():
	for cls in classes:
		bpy.utils.register_class(cls)

def unregister():
	for cls in classes:
		bpy.utils.unregister_class(cls)
