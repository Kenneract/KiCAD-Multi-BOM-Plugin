# Author: Kennan (Kenneract)
# Updated: Apr.03.2024
# API Reference: https://github.com/janelia-pypi/kicad_netlist_reader/blob/main/kicad_netlist_reader/kicad_netlist_reader.py
PLUGIN_VERSION = "Apr.03.2024 (V1.0.8)"

"""
    @package
    Written by Kennan for KiCAD 7.0 and Python 3.7+ (Version 1.0.8).
    
    Generates multiple CSV BoM files for each component distributor you plan
    to purchase from, based on "part number" fields on each symbol. Components
    are sorted by REF and grouped by value & footprint fields. Components
    without a defined distributor will be placed in an "Orphan" BoM file.

    Highlights potential issues (such as doubled distributor fields and
    inconsistent part number usage) during BoM generation, as well as saving
    it to a report file.

    If using JLCPCB part numbers, the plugin can perform a "sanity-check" of
    values/footprints for relevant components by using the provided
    "JLCPCB_Part_Database.csv" file (must be placed in your plugin directory).
	
    CURRENTLY SUPPORTS:

    > JLCPCB
        - "LCSC", or "LCSC Part", "JLCPCB" fields
        - Outputs JLCPCB PCBA compatible BoM
        - Columns: "Comment", "Designator", "Footprint", "LCSC Part #"

    > Digikey
        - "Digikey", "Digi-Key", or "Digi-Key_PN" fields
        - Output BoM can be used with Digikey's Parts List Manager
        - Columns: "Customer Reference", "Note", "Reference Designator",
                    "Footprint", "Digi-Key Part Number", "Quantity"
	
    Command Line:
    python "pathToFile/bom_csv_multi_distributor.py" "%I"
"""

import time
START_TIME = time.time() #script start time

import kicad_netlist_reader
import csv, sys
from os import path, remove
from dataclasses import dataclass


JLCPCB_PART_FILE = "JLCPCB_Part_Database.csv"

JLCPCB_FIELDS = ("LCSC", "LCSC Part", "JLCPCB")
DIGIKEY_FIELDS = ("Digikey", "Digi-Key", "Digi-Key_PN")

JLCPCB_BOM_FILE = "{0}_BOM_JLCPCB.csv"
DIGIKEY_BOM_FILE = "{0}_BOM_Digikey.csv"
ORPHAN_BOM_FILE = "{0}_BOM_Orphaned.csv"
REPORT_FILE = "{0}_BOM_Report.txt"


@dataclass
class CachedJLCPCBPart:
    # For storing data about components found in the schematic
    jlcpcbNum: str
    ref: str
    value: str
    footprint: str

def resolveValue(value:str):
    """
    Given a string with a value & units (e.g. "20mH or 3k3"),
    resolves and returns the raw number (e.g. 0.02 or 3300).

    Output is useful for direct comparisons and hashmaps, not
    nessesarily for human reading.

    Returns None if evaluation fails. Not particularly efficient.
    """
    try:
        magnitudes = {"p":-12, "n":-9, "u":-6, "µ":-6, "m":-3, "k":3, "M":6, "G":9}
        # Remove units
        val = ""
        value = value.replace(",",".") #dot for decimal place
        for c in value:
            if (c in magnitudes or c.isdigit() or c == "."):
                val += c
        
        # Interpret magnitude
        mag = 0
        for c in val:
            if c in magnitudes:
                mag = magnitudes[c]
                break

        # Isolate raw number
        value = ""
        decDone = False
        for c in val:
            if (c.isdigit()):
                value += c
            elif (not decDone and (c == "." or c in magnitudes)):
                value += "."
                decDone = True
        value = float(value)

        # Calculate result
        return round(value * (10**mag), 15)
    except:
        return None

def onlyAlphanum(inStr):
    """
    Given a string, returns the string with all =
    non-alphanumeric characters removed.
    """
    return "".join(ch for ch in inStr if ch.isalnum())

class JLCPCBPartData():
    """
    A representation of a JLCPCB part.
    """
    def __init__(self, partNum, type, value, footprint, edited:int, isBasic):
        self.partNum = partNum
        self.type = type
        self.value = value
        self.rawValue = resolveValue(value)
        self.footprint = footprint
        self.edited = edited
        self.isBasic = isBasic

    def getIsBasic(self):
        return self.isBasic

    def getPartNum(self):
        return self.partNum

    def getType(self):
        return self.type

    def checkMatchType(self, inType):
        """
        Checks if the given type matches for this part.

        Performs some text processing.
        """
        # Process incoming type
        cleanType = ""
        for c in inType:
            if (c.isalpha()):
                cleanType += c
        # Compare
        return self.type.lower() == cleanType.lower()

    def getValue(self):
        return self.value

    def getRawValue(self):
        if (self.rawValue is not None):
            return self.rawValue
        else:
            return self.getValue()

    def checkMatchValue(self, inVal):
        """
        Checks if the given value matches for
        this part. Performs some processing.
        """
        # Process incoming value
        cleanVal = resolveValue(inVal)
        if (cleanVal is None):
            cleanVal = inVal
        # Ensure have raw value
        rawVal = self.rawValue
        if (rawVal is None):
            rawVal = self.value
        # Compare
        #rawVal = onlyAlphanum(str(rawVal)).upper()
        #cleanVal = onlyAlphanum(str(cleanVal)).upper()
        #print(f"{rawVal} in {cleanVal}")
        return str(rawVal) in str(cleanVal)

    def getFootprint(self):
        return self.footprint

    def checkMatchFootprint(self, inFoot):
        """
        Checks if the given footprint matches for
        this part. Not very accurate.

        Performs some text processing.
        """
        # Process incoming footprint
        cleanFoot = inFoot.split(":")[-1]
        # Remove special chars
        cleanFoot = onlyAlphanum(cleanFoot).upper()
        selfFoot = onlyAlphanum(self.footprint).upper()
        # Compare
        return selfFoot in cleanFoot

    def checkMatchCachedPart(self, part:CachedJLCPCBPart):
        """
        Compares this part to a given cached JLCPCB part.

        Returns an empty string if part matches, returns an error
        string if they don't match.
        """
        # Check matches
        mType = self.checkMatchType(part.ref)
        mValue = self.checkMatchValue(part.value)
        mFoot = self.checkMatchFootprint(part.footprint)
        # Generate output
        if (self.type != "" and not mType):
            return f"[{part.ref}] is expected to be type \"{self.type}\""
        if (self.value != "" and not mValue):
            return f"[{part.ref}]'s value is \"{part.value}\", expected \"{self.value}\""
        if (self.footprint != "" and not mFoot):
            return f"[{part.ref}]'s footprint is \"{part.footprint}\", expected \"{self.footprint}\""
        return ""

    def getDateEdited(self):
        """
        Returns the date this part was edited in the database.

        Returns integer of form YYYYMMDD. Returns None if undefined.
        """
        return self.edited


class JLCPCBPartDatabase():
    """
    A database of JLCPCB parts.
    """
    def __init__(self, source:str):
        """
        Initializes the database & loads from disk.
        """
        self.parts = {}
        self.lastUpdate = 0 # YYYYMMDD of last update
        # Load data from database
        with open(source, "r") as file:
            csvReader = csv.DictReader(file)
            for row in csvReader:
                # Pull data from row
                pNum = row["Number"]
                pType = row["Type"]
                pVal = row["Value"]
                pFoot = row["Footprint"]
                pIsBasic = (row["Basic"] != "0")
                pEdited = None
                if ("Edited" in row):
                    pEdited = row["Edited"]
                if (pEdited is not None and len(pEdited)>0):
                    pEdited = int(pEdited)
                    self.lastUpdate = max(self.lastUpdate, pEdited)
                # Create JLCPCB Part & add to parts list
                part = JLCPCBPartData(pNum, pType, pVal, pFoot, pEdited, pIsBasic)
                self.parts.update( {pNum : part} )
        # Cache a dict of parts based on values & footprints (for quick lookups)
        self.partsLookup = {}
        for pNum in self.parts:
            part = self.parts[pNum]
            partVal = part.getRawValue()
            partFoot = part.getFootprint() #TODO: Make this the resolved/raw footprint in the future
            hash = f"{partVal}{partFoot}"
            if (hash in self.partsLookup):
                self.partsLookup[hash].append(part)
            else:
                self.partsLookup[hash] = [part]

    def getBasicPartNum(self, value=None, footprint=None, cachedPart:CachedJLCPCBPart=None):
        """
        Given a value and a footprint (or a CachedJLCPCBPart), returns
        a JLCPCB Basic part number with those properties. If cannot find
        a match, returns None.

        Should have complexity of O(1)
        """
        # Load values if a cached part
        if (cachedPart is not None):
            value = cachedPart.value
            footprint = cachedPart.footprint
        # Clean data & make hash
        indVal = resolveValue(value)
        footprint = footprint
        hash = f"{indVal}{footprint}"
        # Search for basic part in database
        if (hash in self.partsLookup):
            parts = self.partsLookup[hash]
            for part in parts:
                if part.getIsBasic():
                    return part.getPartNum()
        return None

    def getNumItem(self):
        """
        Returns the number of parts in database
        """
        return len(self.parts)

    def getLastUpdate(self, string:bool=False):
        """
        Returns the date the database file was last updated.

        Returns as integer of form YYYYMMDD (pretty=False) or
        a String of form "YYYY.MM.DD"
        """
        if (string):
            if (len(str(self.lastUpdate)) != 8):
                return "?"
            else:
                y = str(self.lastUpdate)[0:4]
                m = str(self.lastUpdate)[4:6]
                d = str(self.lastUpdate)[6:]
                return f"{y}.{m}.{d}"
        else:
            return self.lastUpdate

    def getPart(self, partNum):
        """
        Returns the given part, if in database.
        Returns None otherwise.
        """
        if (partNum in self.parts):
            return self.parts[partNum]
        return None


def checkFields(component, fields:tuple, ignoreCase:bool=True):
    """
    Checks if the given KiCAD component has any of the
    provided fields. If a match is found, the value
    from the first matching field is returned. Returns
    None if no matches found.

    Can optionally ignore the case of the fields,
    though has O(n*m) complexity.
    """
    if (ignoreCase):
        # CASE-INSENSITIVE SEARCH
        fieldNames = component.getFieldNames()
        for compField in fieldNames:
            for inField in fields:
                if compField.lower() == inField.lower():
                    return component.getField(compField).strip()
        return None
    else:
        # CASE-SENSITIVE SEARCH
        for inField in fields:
            val = component.getField(inField).strip()
            if val != "":
                return val
        return None

def deleteFile(file:str):
    """
    Attempts to delete the given file.
    """
    try:
        remove(file)
    except FileNotFoundError:
        pass


# Resolve environment data
projName = path.basename(sys.argv[1]).strip(".xml")
projDir = path.dirname(sys.argv[1])
pluginDir = path.dirname(path.abspath(__file__))
jlcpcbDataFile = path.join(pluginDir, JLCPCB_PART_FILE)

# Delete existing BoM / report files
reportFile = path.join(projDir, REPORT_FILE.format(projName))
jlcpcbFile = path.join(projDir, JLCPCB_BOM_FILE.format(projName))
digikeyFile = path.join(projDir, DIGIKEY_BOM_FILE.format(projName))
orphanFile = path.join(projDir, ORPHAN_BOM_FILE.format(projName))
deleteFile(reportFile)
deleteFile(jlcpcbFile)
deleteFile(digikeyFile)
deleteFile(orphanFile)

# Load JLCPCB database
jlcDB = None
if path.exists(jlcpcbDataFile):
    jlcDB = JLCPCBPartDatabase(jlcpcbDataFile)

# Read KiCAD netlist
net = kicad_netlist_reader.netlist(sys.argv[1])

# Lists of rows for distributor CSV files
jlcpcbRows = []
digikeyRows = []
orphanRows = []

# List of warnings for report
warnings = []

# JLCPCB parts data (if caching)
jlcpcbItems = []

# Iterate through all component groups (grouped when matching Value, Library, & Footprint I think)
for group in net.groupComponents():
    # Dicts of {PartNum:[RefList]} for each distributor
    jlcpcbPartRefs = {}
    digikeyPartRefs = {}
    orphanRefs = []

    # Populate CSV rows with component details for each group
    for component in group:
        # Check for known fields on this component
        comp = component
        jlcpcbPartNum = checkFields(comp, JLCPCB_FIELDS)
        digikeyPartNum = checkFields(comp, DIGIKEY_FIELDS)

        distributors = [(jlcpcbPartNum, jlcpcbPartRefs),
                        (digikeyPartNum, digikeyPartRefs)]

        # Record REF to parts dictionary for appropriate distributor
        orphaned = True
        for distPartNum, distPartRefs in distributors:
            if (distPartNum is not None):
                # Part belongs to this distributor
                if (not orphaned):
                    # Already found distributor; multiple defined!
                    msg = f"WARN: [{comp.getRef()}] has multiple distributors defined (only using first found)"
                    warnings.append(msg)
                    break
                else:
                    # Add REF to its parts dictionary
                    if (distPartNum in distPartRefs): 
                        distPartRefs[distPartNum].append(comp.getRef())
                    else:
                        distPartRefs[distPartNum] = [comp.getRef()]
                    orphaned = False

        # If no distributor found, record part as orphan
        if (orphaned):
            orphanRefs.append(comp.getRef())

        # Cache JLCPCB Part details for later (if enabled)
        if (jlcDB is not None and jlcpcbPartNum is not None):
            p = CachedJLCPCBPart(jlcpcbPartNum, comp.getRef(), comp.getValue(),
                                comp.getFootprint().split(":")[-1])
            jlcpcbItems.append(p)

    # All components in group processed; collect group info
    value = comp.getValue()
    desc = comp.getDescription()
    footprint = comp.getFootprint().split(":")[-1]
    distributorParts = [jlcpcbPartRefs, digikeyPartRefs]

    # Check for grouped (identical) parts with differing part numbers
    for parts in distributorParts:
        if (len(parts) > 1):
            offenders = ["=".join(parts[pNum]) for pNum in parts]
            msg = f"WARN: Symbols [{', '.join(offenders)}] are identical but have different part numbers"
            warnings.append(msg)

    # Generate rows for CSV BOM files
    for jlcpcbPartNum in jlcpcbPartRefs:
        # Comment, REFs, Footprint, JLCPCB Part #
        jlcpcbRows.append([value+" "+desc, ",".join(jlcpcbPartRefs[jlcpcbPartNum]),
                            footprint, jlcpcbPartNum])
    for digikeyPartNum in digikeyPartRefs:
        # Value, Description, REFs, Footprint, Digi-Key Part Number, Quantity
        digikeyRows.append([value, desc, ",".join(digikeyPartRefs[digikeyPartNum]),
                            footprint, digikeyPartNum, len(digikeyPartRefs[digikeyPartNum])])
    if (len(orphanRefs) > 0):
        # Comment, REFs, Footprint
        orphanRows.append([value+" "+desc, ",".join(orphanRefs), footprint])


# Write row data to CSV BOM files

# JLCPCB
if (len(jlcpcbRows) > 0):
    with open(jlcpcbFile, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for row in jlcpcbRows:
            out.writerow(row)
# Digikey
if (len(digikeyRows) > 0):
    with open(digikeyFile, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["Customer Reference", "Note", "Reference Designator", "Footprint",
                        "Digi-Key Part Number", "Quantity"])
        for row in digikeyRows:
            out.writerow(row)
# Orphans
if (len(orphanRows) > 0):
    with open(orphanFile, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["Comment", "Designator", "Footprint"])
        for row in orphanRows:
            out.writerow(row)

# Run JLCPCB Parts Sanity-Check
jlcpcbSanityNotes = []
jlcpcbSanitySuggestions = []
jlcpcbSanityMissing = []
numPass = 0
numFail = 0
numUkn = 0
if (jlcDB is not None and len(jlcpcbRows)>0):
    # Load parts database
    db = JLCPCBPartDatabase(jlcpcbDataFile)

    # Iterate through all parts
    for jlcpcbPart in jlcpcbItems:
        # Get part from JLCPCB database
        part = db.getPart(jlcpcbPart.jlcpcbNum)
        if (part is None):
            # Note that part is unknown
            numUkn += 1
            jlcpcbSanityMissing.append(f"{jlcpcbPart.jlcpcbNum} ({jlcpcbPart.ref}) not in database")
            # Check if a known part could be used
            pNum = db.getBasicPartNum(cachedPart=jlcpcbPart)
            if (pNum is not None):
                msg = f"ALT: [{jlcpcbPart.ref}] Part {pNum} (Basic) could replace {jlcpcbPart.jlcpcbNum} (Unknown)"
                jlcpcbSanitySuggestions.append(msg)

            continue
        # Check if database part matches the schematic
        res = part.checkMatchCachedPart(jlcpcbPart)
        if (res == ""):
            numPass += 1
            # For extended parts, check if a suitable Basic part is available
            if not part.getIsBasic():
                pNum = db.getBasicPartNum(cachedPart=jlcpcbPart)
                if (pNum is not None):
                    msg = f"ALT: [{jlcpcbPart.ref}] Part {pNum} (Basic) could replace {part.partNum} (Extended)"
                    jlcpcbSanitySuggestions.append(msg)
        else:
            # Part fails
            numFail += 1
            jlcpcbSanityNotes.append(res)


# Generate report
reportLines = []
kVer = net.getTool().split(" ")[-1]
pVer = sys.version_info
pVer = f"{pVer.major}.{pVer.minor}.{pVer.micro}"
execTime = f"{(time.time() - START_TIME) * 1000:.1f}"

reportLines.append("# Multi-Distributor BoM Report #\n")
reportLines.append(f"Project Name: {projName} (has {len(net.components)} symbols)")
reportLines.append(f"KiCad Version: {kVer} (Python {pVer})")
reportLines.append(f"Report Generated: {net.getDate()}")
reportLines.append(f"Plugin Version: {PLUGIN_VERSION}")
reportLines.append(f"Execution Time: {execTime}ms\n")
reportLines.append("- "*25 + "\n")

distribData = [("JLCPCB",jlcpcbRows), ("Digikey",digikeyRows),
                ("Orphaned",orphanRows)]
for (name, rows) in distribData:
    reportLines.append(f"{name} BoM: {len(rows)>0}")
    if (len(rows)>0): reportLines[-1] += f" ({len(rows)} rows)"
reportLines.append("") #newline
reportLines.append("- "*25 + "\n")

reportLines.append("BoM Generation Notes:")
if (len(warnings) == 0):
    reportLines.append("(none)")
else:
    reportLines.append("\n".join([f"- {t}" for t in warnings]))
reportLines.append("")
reportLines.append("- "*25 + "\n")

reportLines.append(f"JLCPCB Parts Database File Present: {jlcDB is not None}")

if (jlcDB is not None):
    reportLines[-1] += f" ({jlcDB.getNumItem()} parts, updated {jlcDB.getLastUpdate(string=True)})"
    reportLines.append("JLCPCB Parts Sanity-Checker Results ")
    reportLines[-1] += f"({numPass} pass, {numFail} suspect, {numUkn} not in database):"
    sanityMsgs = jlcpcbSanityNotes+jlcpcbSanitySuggestions+jlcpcbSanityMissing
    if (len(sanityMsgs) == 0):
        reportLines.append("(no notes)")
    else:
        reportLines.append("\n".join([f"- {t}" for t in sanityMsgs]))
else:
    reportLines.append(f"Place the \"{JLCPCB_PART_FILE}\" file in your plugin directory to use this feature.")
    reportLines.append(f"\t({pluginDir})")

# Write report to disk
with open(reportFile, "w") as f:
    f.write("\n".join(reportLines))

# Print output
print(f"\nDONE - printing \"{REPORT_FILE.format(projName)}\" to console:\n")
print("\n".join(reportLines))
