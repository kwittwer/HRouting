"""
QElektrotech (.qet) Export Module

Konvertiert HRouting-Projektdaten zu QElektrotech XML-Format für externe Bearbeitung.

QET-Dateiformat:
- .qet ist ein ZIP-Archive mit XML-Dateien
- Struktur: project.xml, diagram-*.xml, elements.xml
- Dieser Export erzeugt vereinfachte QET-XML
"""

import json
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
from datetime import datetime
import zipfile
import io


class QETExporter:
    """Konvertiert HRouting-Daten zu QElektrotech XML."""
    
    # QET Standard-Abmessungen und Offsets
    ELEMENT_WIDTH = 40
    ELEMENT_HEIGHT = 40
    GRID_SIZE = 10  # QET nutzt 10px-Raster
    
    # Farben für Netze/Potentiale
    NET_COLORS = {
        "L1": {"hex": "FF0000", "name": "Rot (L1)"},       # Rot
        "L2": {"hex": "FFA500", "name": "Orange (L2)"},    # Orange
        "L3": {"hex": "FFFF00", "name": "Gelb (L3)"},      # Gelb
        "N": {"hex": "0000FF", "name": "Blau (N)"},        # Blau
        "PE": {"hex": "00AA00", "name": "Grün (PE)"},      # Grün
    }
    
    # Geräte-Typ-Mappings
    DEVICE_TYPE_MAP = {
        "Steckdose": "Socket",
        "Schalter": "Switch",
        "Leuchte": "Lamp",
        "Taster": "Button",
        "Schütz": "Contactor",
        "Relais": "Relay",
        "Motor": "Motor",
        "Abzweigdose": "JunctionBox",
    }
    
    def __init__(self, project_dict: Dict[str, Any]):
        """
        Args:
            project_dict: HRouting-Projektdaten (canvas + params)
        """
        self.project = project_dict
        self.canvas = project_dict.get("canvas", {})
        self.params = project_dict.get("params", {})
        
        # Tracking
        self.elements = []  # Liste der QET-Elemente
        self.connections = []  # Leitungsverbindungen
        self.diagrams = {}  # Seiten nach Typ
        
    def export_to_qet(self, output_path: str) -> bool:
        """
        Exportiert HRouting-Projekt zu QET-Datei.
        
        Args:
            output_path: Ziel-Dateipfad (.qet)
            
        Returns:
            True wenn erfolgreich, False sonst
        """
        try:
            # Daten konvertieren
            self._collect_elements()
            self._collect_connections()
            
            # QET-XML erzeugen
            project_xml = self._create_project_xml()
            diagram_xmls = self._create_diagram_xmls()
            
            # ZIP-Archive erstellen
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # project.xml
                zf.writestr('project.xml', project_xml)
                
                # diagram-*.xml
                for idx, (diagram_type, xml_content) in enumerate(diagram_xmls.items()):
                    zf.writestr(f'diagram-{idx}.xml', xml_content)
                
                # metadata
                zf.writestr('metadata.xml', self._create_metadata_xml())
            
            return True
            
        except Exception as e:
            print(f"QET Export Fehler: {e}")
            return False
    
    def _collect_elements(self):
        """Sammelt HRouting-Elemente (APs, UVs, Geräte) für QET."""
        elec_points = self.params.get("elec_points", {})
        elec_rooms = self.params.get("elec_rooms", {})
        
        # Elektro-Anschlusspunkte (APs)
        for ap_id, ap_data in elec_points.items():
            element = self._ap_to_qet_element(ap_id, ap_data)
            if element:
                self.elements.append(element)
        
        # Elektro-Räume (als Verteiler/Schrank)
        for room_id, room_data in elec_rooms.items():
            element = self._room_to_qet_element(room_id, room_data)
            if element:
                self.elements.append(element)
    
    def _collect_connections(self):
        """Sammelt Leitungsverbindungen (Kabel, Netze)."""
        elec_cables = self.params.get("elec_cables", {})
        elec_points = self.params.get("elec_points", {})
        
        # Kabel als Leitungen
        for cable_id, cable_data in elec_cables.items():
            connection = self._cable_to_qet_connection(cable_id, cable_data, elec_points)
            if connection:
                self.connections.append(connection)
    
    def _ap_to_qet_element(self, ap_id: str, ap_data: Dict) -> Optional[Dict]:
        """Konvertiert einen AP zu QET-Element."""
        if not ap_data.get("visible", True):
            return None
        
        name = ap_data.get("name", ap_id)
        ap_type = ap_data.get("builtin_symbol", "Steckdose")
        
        # Position aus canvas
        canvas_pos = self.canvas.get("elec_points", {}).get(ap_id)
        if not canvas_pos:
            return None
        
        element = {
            "id": ap_id,
            "name": name,
            "type": self.DEVICE_TYPE_MAP.get(ap_type, ap_type),
            "x": int(canvas_pos[0]),
            "y": int(canvas_pos[1]),
            "width": ap_data.get("width", self.ELEMENT_WIDTH),
            "height": ap_data.get("height", self.ELEMENT_HEIGHT),
            "builtin_symbol": ap_type,
            "color": ap_data.get("color", "#4fc3f7"),
        }
        
        return element
    
    def _room_to_qet_element(self, room_id: str, room_data: Dict) -> Optional[Dict]:
        """Konvertiert einen Elektro-Raum zu QET-Element (Verteiler/Schrank)."""
        if not room_data.get("visible", True):
            return None
        
        name = room_data.get("name", room_id)
        
        # Schwerpunkt des Polygons
        polygon = self.canvas.get("elec_rooms", {}).get(room_id, [])
        if not polygon or len(polygon) < 3:
            return None
        
        center_x = sum(p[0] for p in polygon) / len(polygon)
        center_y = sum(p[1] for p in polygon) / len(polygon)
        
        element = {
            "id": room_id,
            "name": name,
            "type": "Cabinet",
            "x": int(center_x),
            "y": int(center_y),
            "width": room_data.get("width", 100),
            "height": room_data.get("height", 100),
            "color": room_data.get("color", "#8d99ae"),
        }
        
        return element
    
    def _cable_to_qet_connection(self, cable_id: str, cable_data: Dict, 
                                  elec_points: Dict) -> Optional[Dict]:
        """Konvertiert ein Kabel zu QET-Leitung."""
        start_ap = cable_data.get("start_ap")
        end_ap = cable_data.get("end_ap")
        
        if not start_ap or not end_ap:
            return None
        
        # Positionen der Start/End-APs
        start_pos = self.canvas.get("elec_points", {}).get(start_ap)
        end_pos = self.canvas.get("elec_points", {}).get(end_ap)
        
        if not start_pos or not end_pos:
            return None
        
        connection = {
            "id": cable_id,
            "start_element": start_ap,
            "end_element": end_ap,
            "start_pos": start_pos,
            "end_pos": end_pos,
            "type": "Cable",
            "color": cable_data.get("color", "#ffffff"),
            "note": cable_data.get("note", ""),
        }
        
        return connection
    
    def _create_project_xml(self) -> str:
        """Erzeugt project.xml für QET."""
        root = ET.Element("project")
        
        # Metadaten
        title = ET.SubElement(root, "title")
        title.text = self.params.get("project_name", "HRouting Export")
        
        date = ET.SubElement(root, "date")
        date.text = datetime.now().isoformat()
        
        description = ET.SubElement(root, "description")
        description.text = "Exportiert von HRouting"
        
        # Seiten
        diagrams = ET.SubElement(root, "diagrams")
        diagram = ET.SubElement(diagrams, "diagram")
        diagram.set("id", "diagram-0")
        diagram.set("title", "Stromlaufplan")
        
        return self._prettify_xml(root)
    
    def _create_diagram_xmls(self) -> Dict[str, str]:
        """Erzeugt diagram-*.xml Dateien."""
        diagrams = {}
        
        # Hauptdiagramm: Stromlaufplan
        root = ET.Element("diagram")
        root.set("title", "Stromlaufplan")
        root.set("width", "1000")
        root.set("height", "800")
        
        # Elemente
        elements_elem = ET.SubElement(root, "elements")
        for elem in self.elements:
            self._add_element_to_xml(elements_elem, elem)
        
        # Leitungen
        connections_elem = ET.SubElement(root, "connections")
        for conn in self.connections:
            self._add_connection_to_xml(connections_elem, conn)
        
        # Netze/Potentiale
        nets_elem = ET.SubElement(root, "nets")
        self._add_nets_to_xml(nets_elem)
        
        diagrams["stromlaufplan"] = self._prettify_xml(root)
        
        return diagrams
    
    def _add_element_to_xml(self, parent: ET.Element, element: Dict):
        """Fügt QET-Element zu XML hinzu."""
        elem = ET.SubElement(parent, "element")
        elem.set("id", element.get("id", ""))
        elem.set("type", element.get("type", "Unknown"))
        elem.set("name", element.get("name", ""))
        elem.set("x", str(element.get("x", 0)))
        elem.set("y", str(element.get("y", 0)))
        elem.set("width", str(element.get("width", self.ELEMENT_WIDTH)))
        elem.set("height", str(element.get("height", self.ELEMENT_HEIGHT)))
        
        color = element.get("color", "#ffffff")
        style = ET.SubElement(elem, "style")
        style.set("fill", color)
        
        label = ET.SubElement(elem, "label")
        label.text = element.get("name", "")
    
    def _add_connection_to_xml(self, parent: ET.Element, connection: Dict):
        """Fügt QET-Leitung zu XML hinzu."""
        conn = ET.SubElement(parent, "connection")
        conn.set("id", connection.get("id", ""))
        conn.set("from", connection.get("start_element", ""))
        conn.set("to", connection.get("end_element", ""))
        
        color = connection.get("color", "#ffffff")
        style = ET.SubElement(conn, "style")
        style.set("stroke", color)
        style.set("stroke-width", "2")
    
    def _add_nets_to_xml(self, parent: ET.Element):
        """Fügt Netze/Potentiale zu XML hinzu."""
        # Standard-Netze
        for net_name, net_info in self.NET_COLORS.items():
            net = ET.SubElement(parent, "net")
            net.set("id", f"net-{net_name}")
            net.set("name", net_name)
            net.set("color", net_info["hex"])
    
    def _create_metadata_xml(self) -> str:
        """Erzeugt metadata.xml."""
        root = ET.Element("metadata")
        
        creator = ET.SubElement(root, "creator")
        creator.text = "HRouting QET Export"
        
        created = ET.SubElement(root, "created")
        created.text = datetime.now().isoformat()
        
        format_version = ET.SubElement(root, "format-version")
        format_version.text = "0.1"
        
        return self._prettify_xml(root)
    
    @staticmethod
    def _prettify_xml(elem: ET.Element) -> str:
        """Formatiert XML schön."""
        rough_string = ET.tostring(elem, encoding='unicode')
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ")


def export_project_to_qet(project_dict: Dict[str, Any], output_path: str) -> Tuple[bool, str]:
    """
    Wrapper-Funktion für QET-Export.
    
    Args:
        project_dict: HRouting-Projektdaten
        output_path: Ziel-Dateipfad
        
    Returns:
        (erfolg: bool, nachricht: str)
    """
    exporter = QETExporter(project_dict)
    
    success = exporter.export_to_qet(output_path)
    
    if success:
        return True, f"Erfolgreich exportiert zu: {output_path}"
    else:
        return False, "Export fehlgeschlagen"
