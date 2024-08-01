############################################################################
## Tool name: Transit Network Analysis Tools
## Created by: Melinda Morang, Esri
## Last updated: 1 August 2024
############################################################################
"""
## TODO
This is a shared module with classes for adding transit information, such
as wait time, ride time, and run information, to a feature class of traversed
edges, or traversal result.  The TransitTraversalResultCalculator class can
be used with a traversal result generated from a network analysis layer or
a Route solver object.

Copyright 2024 Esri
   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at
       http://www.apache.org/licenses/LICENSE-2.0
   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""
import os
import arcpy
from AnalysisHelpers import TransitNetworkAnalysisToolsError


class TransitDataModel:  # pylint: disable=too-many-instance-attributes
    """Defines and validates the public transit data model as relevant to this tool."""

    def __init__(self, transit_fd: str):
        """Define the public transit data model as relevant to this tool."""
        # For details on the public transit data model, see
        # https://pro.arcgis.com/en/pro-app/latest/help/analysis/networks/transit-data-model.htm
        self.line_variant_elements = os.path.join(transit_fd, "LineVariantElements")
        self.lve_shapes = os.path.join(transit_fd, "LVEShapes")
        self.required_tables = [self.line_variant_elements, self.lve_shapes]
        self.required_fields = {
            self.line_variant_elements: ["LVEShapeID"],
            self.lve_shapes: ["ID"]
        }

    def validate_tables_exist(self):
        """Validate that the required public transit data model feature classes and tables exist.

        Raises:
            TransitNetworkAnalysisToolsError: If not all required fields are present.
        """
        # Check for required feature classes and tables
        tables_exist = True
        for table in self.required_tables:
            if not arcpy.Exists(table):
                tables_exist = False
        if not tables_exist:
            # One or more public transit data model tables does not exist.
            raise TransitNetworkAnalysisToolsError(
                arcpy.GetIDMessage(2922) + f" Required: LineVariantElements, LVEShapes")

    def validate_required_fields(self):
        """Validate that the transit data model feature classes and tables have the required fields for this tool.

        Raises:
            TransitNetworkAnalysisToolsError: If not all required fields are present.
        """
        for table in self.required_fields:
            # Compare in lower case because SDE switches the case around. Oracle is all upper. Postgres is all lower.
            required_fields_lower = [f.lower() for f in self.required_fields[table]]
            actual_fields = [f.name.lower() for f in arcpy.ListFields(table)]
            if not set(required_fields_lower).issubset(set(actual_fields)):
                # Public transit data model table %1 is missing one or more required fields. Required fields: %2
                msg = arcpy.GetIDMessage(2925) % (table, ", ".join(self.required_fields[table]))
                raise TransitNetworkAnalysisToolsError(msg)


class RouteShapeReplacer:
    """Enrich an ordinary traversal result with public transit info."""

    def __init__(
        self, traversed_edges_fc, transit_fd, out_fc
    ):
        """Initialize the calculator for the given analysis.

        Args:
            traversed_edges_fc (str or layer): Feature class layer or catalog path containing the Edges portion of a
                traversal result. Typically obtained from the Copy Traversed Source Features tool or the RouteEdges
                output from a solver result object.
            analysis_datetime (datetime): The date and time of the network analysis, typically obtained from the layer
                or solver object analysis properties.
            analysis_time_type (AnalysisTimeType): Defines how to interpret the analysis_datetime.
            transit_fd (str): Catalog path to the feature dataset containing the transit-enabled network dataset used
                for the analysis and its associated Public Transit Data Model feature classes.
            travel_mode (arcpy.nax.TravelMode): Travel mode used for the analysis. Should be passed as a travel mode
                object and not a string name.
            route_id_field (str): Field name separating routes in the traversed edges feature class.  RouteID for Route
                and Closest Facility analysis; FacilityID for Service Area.
            use_impedance_in_field_names (bool): Whether to use field names of the form Attr_[impedance] and
                Cumul_[impedance] (as for NA layers) (True) or use the standard Attr_Minutes and Cumul_Minutes
                (as for solver objects) (False)
        """
        self.traversed_edges_fc = traversed_edges_fc
        self.out_fc = out_fc

        # Validate basic inputs
        if not isinstance(transit_fd, str):
            raise TransitNetworkAnalysisToolsError("Invalid Public Transit Data Model feature dataset.")

        # Initialize the Public Transit Data Model tables
        self.transit_dm = TransitDataModel(transit_fd)
        # Validate Public Transit Data Model
        self.transit_dm.validate_tables_exist()
        self.transit_dm.validate_required_fields()

        # Validate traversal result
        if not arcpy.Exists(self.traversed_edges_fc):
            raise TransitNetworkAnalysisToolsError(
                f"The input traversed edges feature class {self.traversed_edges_fc} does not exist.")
        self.te_desc = arcpy.Describe(self.traversed_edges_fc)
        required_fields = ["SourceName", "SourceOID", "RouteID"]
        if not set(required_fields).issubset(set([f.name for f in self.te_desc.fields])):
            raise TransitNetworkAnalysisToolsError((
                f"The input traversed edges feature class {self.traversed_edges_fc} is missing one or more required "
                f"fields. Required fields: {required_fields}"
            ))

    def replace_route_shapes_with_lveshapes(self) -> bool:
        """Replace route shape geometry.
        """
        # Make layers to speed up search cursor queries later
        lve_lyr_name = "LineVariantElements"
        arcpy.management.MakeFeatureLayer(self.transit_dm.line_variant_elements, lve_lyr_name)
        lve_oid_field = arcpy.Describe(lve_lyr_name).oidFieldName
        lveshapes_lyr_name = "LVEShapes"
        arcpy.management.MakeFeatureLayer(self.transit_dm.lve_shapes, lveshapes_lyr_name)

        # Loop over traversed route segments and replace LineVariantElements geometry with LVEShapes geometry
        route_segments = {}
        fields = ["RouteID", "SHAPE@", "SourceName", "SourceOID"]
        for row in arcpy.da.SearchCursor(self.traversed_edges_fc, fields):  # pylint: disable=no-member
            segment_geom = row[1]
            if row[2] == "LineVariantElements":
                # Retrieve LVEShapes geometry
                with arcpy.da.SearchCursor(lve_lyr_name, ["LVEShapeID"], f"{lve_oid_field} = {row[3]}") as cur:
                    lveshape_id = next(cur)[0]
                with arcpy.da.SearchCursor(lveshapes_lyr_name, ["SHAPE@"], f"LVEShapeID = {lveshape_id}") as cur:
                    ## TODO: Check for case when no rows are returned
                    lveshape_geom = next(cur)[0]
                    if lveshape_geom:
                        segment_geom = lveshape_geom

            # Store the route segment geometry as an array of vertices we'll use to construct the final polylines
            # getPart() retrieves an array of arrays of points representing the vertices of the polyline.
            for part in segment_geom.getPart():
                route_segments.setdefault(row[0], arcpy.Array()).extend(part)

        # Combine route segments and write to output feature class
        ## TODO: Probably just return the geometries instead of a new feature class so we can do an UpdateCursor on the
        ## original inputs
        arcpy.management.CreateFeatureclass(
            os.path.dirname(self.out_fc),
            os.path.basename(self.out_fc),
            "POLYLINE",
            spatial_reference=self.te_desc.spatialReference
        )
        arcpy.management.AddField(self.out_fc, "RouteID", "LONG")
        with arcpy.da.InsertCursor(self.out_fc, ["SHAPE@", "RouteID"]) as cur:
            for route_id, vertex_array in route_segments.items():
                route_geom = arcpy.Polyline(vertex_array, self.te_desc.spatialReference)
                cur.insertRow([route_geom, route_id])


if __name__ == "__main__":
    pass
