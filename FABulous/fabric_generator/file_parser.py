import csv
import logging
import math
import os
import re
import shutil
from copy import deepcopy
from typing import Dict, List, Literal, Tuple, Union, overload

from FABulous.fabric_generator.fabric import (IO, Bel, ConfigBitMode, ConfigMem,
                                      Direction, Fabric, MultiplexerStyle,
                                      Port, Side, SuperTile, Tile)

oppositeDic = {"NORTH": "SOUTH", "SOUTH": "NORTH",
               "EAST": "WEST", "WEST": "EAST"}

logger = logging.getLogger(__name__)

def parseFabricCSV(fileName: str) -> Fabric:
    """
    Pares a csv file and returns a fabric object.

    Args:
        fileName (str): the directory of the csv file.

    Raises:
        ValueError: File provide need to be a csv file.
        ValueError: The csv file does not exist.
        ValueError: Cannot find the FabricBegin and FabricEnd region.
        ValueError: Cannot find the ParametersBegin and ParametersEnd region.
        ValueError: The bel entry extension can only be ".v" or ".vhdl".
        ValueError: The matrix entry extension can only be ".list", ".csv", ".v" or ".vhdl".
        ValueError: Unknown tile description entry in csv file.
        ValueError: Unknown tile in the fabric entry in csv file.
        ValueError: Unknown super tile in the super tile entry in csv file.
        ValueError: Invalid ConfigBitMode in parameter entry in csv file.
        ValueError: Invalid MultiplexerStyle in parameter entry in csv file.
        ValueError: Invalid parameter entry in csv file.

    Returns:
        Fabric: The fabric object.
    """
    if not fileName.endswith(".csv"):
        raise ValueError("File must be a csv file")

    if not os.path.exists(fileName):
        raise ValueError(f"File {fileName} does not exist")

    filePath, _ = os.path.split(os.path.abspath(fileName))

    with open(fileName, 'r') as f:
        file = f.read()
        file = re.sub(r"#.*", "", file)

    # read in the csv file and part them
    if fabricDescription := re.search(
            r"FabricBegin(.*?)FabricEnd", file, re.MULTILINE | re.DOTALL):
        fabricDescription = fabricDescription.group(1)
    else:
        raise ValueError(
            'Cannot find FabricBegin and FabricEnd in csv file')

    if parameters := re.search(
            r"ParametersBegin(.*?)ParametersEnd", file, re.MULTILINE | re.DOTALL):
        parameters = parameters.group(1)
    else:
        raise ValueError(
            'Cannot find ParametersBegin and ParametersEnd in csv file')

    tilesData = re.findall(r"TILE(.*?)EndTILE", file,
                           re.MULTILINE | re.DOTALL)

    superTile = re.findall(r"SuperTILE(.*?)EndSuperTILE",
                           file, re.MULTILINE | re.DOTALL)

    # parse the tile description
    fabricDescription = fabricDescription.split("\n")
    parameters = parameters.split("\n")
    tileTypes = []
    tileDefs = []
    commonWirePair: List[Tuple[str, str]] = []
    for t in tilesData:
        t = t.split("\n")
        tileName = t[0].split(",")[1]
        tileTypes.append(tileName)
        ports: List[Port] = []
        bels: List[Bel] = []
        tileCarry: Dict[IO, str] = {}
        matrixDir = ""
        withUserCLK = False
        configBit = 0
        genMatrixList = False
        for item in t:
            temp: List[str] = item.split(",")
            if not temp or temp[0] == "":
                continue
            if temp[0] in ["NORTH", "SOUTH", "EAST", "WEST"]:
                ports.append(Port(Direction[temp[0]], temp[1], int(
                    temp[2]), int(temp[3]), temp[4], int(temp[5]), temp[1], IO.OUTPUT, Side[temp[0]]))

                ports.append(Port(Direction[temp[0]], temp[1], int(
                    temp[2]), int(temp[3]), temp[4], int(temp[5]), temp[4], IO.INPUT, Side[oppositeDic[temp[0]].upper()]))

                if temp[6] == "CARRY":
                    if not tileCarry:
                        #TODO add a counter for wirecount
                        #  tileCarry[IO.OUTPUT] = f"{temp[1]}{temp[5]}"
                        #  tileCarry[IO.INPUT] = f"{temp[4]}{temp[5]}"
                        tileCarry[IO.OUTPUT] = f"{temp[1]}0"
                        tileCarry[IO.INPUT] = f"{temp[4]}0"
                    else:
                        raise ValueError(f"You can only define one carrychain per Tile! \
                        {temp[1]}/{temp[4]} can't be added as carryOut/carryIn, \
                        since there are already {tileCarry[IO.INPUT]}/{tileCarry[IO.OUTPUT]}. \
                        Please check your fabric.csv!")

                commonWirePair.append(
                    (f"{temp[1]}", f"{temp[4]}"))

            elif temp[0] == "JUMP":
                ports.append(Port(Direction.JUMP, temp[1], int(
                    temp[2]), int(temp[3]), temp[4], int(temp[5]), temp[1], IO.OUTPUT, Side.ANY))
                ports.append(Port(Direction.JUMP, temp[1], int(
                    temp[2]), int(temp[3]), temp[4], int(temp[5]), temp[4], IO.INPUT, Side.ANY))
            elif temp[0] == "BEL":
                belFilePath = os.path.join(filePath, temp[1])
                if temp[1].endswith(".vhdl"):
                    result = parseFileVHDL(belFilePath, temp[2])
                elif temp[1].endswith(".v"):
                    result = parseFileVerilog(belFilePath, temp[2])
                else:
                    raise ValueError(
                        "Invalid file type, only .vhdl and .v are supported")
                internal, external, config, shared, configBit, userClk, belMap, belCarry = result
                bels.append(Bel(belFilePath, temp[2], internal,
                            external, config, shared, configBit, belMap, userClk, belCarry))
                withUserCLK |= userClk
            elif temp[0] == "MATRIX":
                matrixDir = os.path.join(filePath, temp[1])
                configBit = 0
                if temp[1].endswith(".list"):
                    for _, v in parseList(matrixDir, "source").items():
                        muxSize = len(v)
                        if muxSize >= 2:
                            configBit += muxSize.bit_length()-1
                elif temp[1].endswith("_matrix.csv"):
                    for _, v in parseMatrix(matrixDir, tileName).items():
                        muxSize = len(v)
                        if muxSize >= 2:
                            configBit += muxSize.bit_length()-1
                elif temp[1].endswith(".vhdl") or temp[1].endswith(".v"):
                    with open(matrixDir, "r") as f:
                        f = f.read()
                        if configBit := re.search(r"NumberOfConfigBits: (\d+)", f):
                            configBit = int(configBit.group(1))
                        else:
                            configBit = 0
                            print(
                                f"Cannot find NumberOfConfigBits in {matrixDir} assume 0 config bits")
                elif temp[1] == "GENERATE":
                    matrixDir = f"{filePath}/Tile/{tileName}/{tileName}_generated_switchmatrix.list"
                    genMatrixList = True
                    #  matrixDir = f"{filePath}/Tile/{tileName}"
                    #  logger.info(f"{tile.name} has no matrix file")
                    #  logger.info(f"bootstrapping {tile.name} to matrix list file")
                    #  geneateSwitchmatrixList(tile)
                    #  generateConfigMem(tile)
                else:
                    raise ValueError(
                        'Unknown file extension for matrix')
            else:
                raise ValueError(
                    f"Unknown tile description {temp[0]} in tile {t}")

        if genMatrixList:
            configBit += generateSwitchmatrixList(tileName, bels, matrixDir, tileCarry)

        tileDefs.append(Tile(tileName, ports, bels,
                        matrixDir, withUserCLK, configBit))

    fabricTiles = []
    tileDic = dict(zip(tileTypes, tileDefs))

    # parse the super tile
    superTileDic = {}
    for t in superTile:
        description = t.split("\n")
        name = description[0].split(",")[1]
        tileMap = []
        tiles = []
        bels = []
        withUserCLK = False
        for i in description[1:-1]:
            line = i.split(",")
            line = [i for i in line if i != "" and i != " "]
            row = []

            if line[0] == "BEL":
                belFilePath = os.path.join(filePath, line[1])
                if line[0].endswith("VHDL"):
                    result = parseFileVHDL(belFilePath, line[2])
                else:
                    result = parseFileVerilog(belFilePath, line[2])
                internal, external, config, shared, configBit, userClk, belMap, belCarry = result
                bels.append(Bel(belFilePath, line[2], internal,
                            external, config, shared, configBit, belMap, userClk, belCarry))
                withUserCLK |= userClk
                continue

            for j in line:
                if j in tileDic:
                    # mark the tile as part of super tile
                    tileDic[j].partOfSuperTile = True
                    t = deepcopy(tileDic[j])
                    row.append(t)
                    if t not in tiles:
                        tiles.append(t)
                elif j == "Null" or j == "NULL" or j == "None":
                    row.append(None)
                else:
                    raise ValueError(
                        f"The super tile {name} contains definitions that are not tiles or Null.")
            tileMap.append(row)

        superTileDic[name] = SuperTile(name, tiles, tileMap, bels, withUserCLK)

    # form the fabric data structure
    usedTile = set()
    for f in fabricDescription:
        fabricLineTmp = f.split(",")
        fabricLineTmp = [i for i in fabricLineTmp if i != ""]
        if not fabricLineTmp:
            continue
        fabricLine = []
        for i in fabricLineTmp:
            if i in tileDic:
                fabricLine.append(deepcopy(tileDic[i]))
                usedTile.add(i)
            elif i == "Null" or i == "NULL" or i == "None":
                fabricLine.append(None)
            else:
                raise ValueError(f"Unknown tile {i}")
        fabricTiles.append(fabricLine)

    for i in list(tileDic.keys()):
        if i not in usedTile:
            print(
                f"Tile {i} is not used in the fabric. Removing from tile dictionary.")
            del tileDic[i]
    for i in list(superTileDic.keys()):
        if any(j.name not in usedTile for j in superTileDic[i].tiles):
            print(
                f"Supertile {i} is not used in the fabric. Removing from tile dictionary.")
            del superTileDic[i]

    # parse the parameters
    height = 0
    width = 0
    configBitMode = ConfigBitMode.FRAME_BASED
    frameBitsPerRow = 32
    maxFramesPerCol = 20
    package = "use work.my_package.all;"
    generateDelayInSwitchMatrix = 80
    multiplexerStyle = MultiplexerStyle.CUSTOM
    superTileEnable = True

    for i in parameters:
        i = i.split(",")
        i = [j for j in i if j != ""]
        if not i:
            continue
        if i[0].startswith("ConfigBitMode"):
            if i[1] == "frame_based":
                configBitMode = ConfigBitMode.FRAME_BASED
            elif i[1] == "FlipFlopChain":
                configBitMode = ConfigBitMode.FLIPFLOP_CHAIN
            else:
                raise ValueError(
                    f"Invalid config bit mode {i[1]} in parameters. Valid options are frame_based and FlipFlopChain")
        elif i[0].startswith("FrameBitsPerRow"):
            frameBitsPerRow = int(i[1])
        elif i[0].startswith("MaxFramesPerCol"):
            maxFramesPerCol = int(i[1])
        elif i[0].startswith("Package"):
            package = i[1]
        elif i[0].startswith("GenerateDelayInSwitchMatrix"):
            generateDelayInSwitchMatrix = int(i[1])
        elif i[0].startswith("MultiplexerStyle"):
            if i[1] == "custom":
                multiplexerStyle = MultiplexerStyle.CUSTOM
            elif i[1] == "generic":
                multiplexerStyle = MultiplexerStyle.GENERIC
            else:
                raise ValueError(
                    f"Invalid multiplexer style {i[1]} in parameters. Valid options are custom and generic")
        elif i[0].startswith("SuperTileEnable"):
            superTileEnable = i[1] == "TRUE"
        else:
            raise ValueError(f"The following parameter is not valid: {i}")

    height = len(fabricTiles)
    width = len(fabricTiles[0])

    commonWirePair = list(dict.fromkeys(commonWirePair))
    commonWirePair = [(i, j) for (
        i, j) in commonWirePair if "NULL" not in i and "NULL" not in j]

    return Fabric(tile=fabricTiles,
                  numberOfColumns=width,
                  numberOfRows=height,
                  configBitMode=configBitMode,
                  frameBitsPerRow=frameBitsPerRow,
                  maxFramesPerCol=maxFramesPerCol,
                  package=package,
                  generateDelayInSwitchMatrix=generateDelayInSwitchMatrix,
                  multiplexerStyle=multiplexerStyle,
                  numberOfBRAMs=int(height/2),
                  superTileEnable=superTileEnable,
                  tileDic=tileDic,
                  superTileDic=superTileDic,
                  commonWirePair=commonWirePair)


@overload
def parseList(fileName: str, collect: Literal["pair"] = "pair") -> List[Tuple[str, str]]:
    pass


@overload
def parseList(fileName: str, collect: Literal["source", "sink"]) -> Dict[str, List[str]]:
    pass


def parseList(fileName: str, collect: Literal["pair", "source", "sink"] = "pair") -> Union[List[Tuple[str, str]], Dict[str, List[str]]]:
    """
    parse a list file and expand the list file information into a list of tuples.

    Args:
        fileName (str): ""
        collect (Literal[&quot;&quot;, &quot;source&quot;, &quot;sink&quot;], optional): Collect value by source, sink or just as pair. Defaults to "pair".

    Raises:
        ValueError: The file does not exist.
        ValueError: Invalid format in the list file.

    Returns:
        Union[List[Tuple[str, str]], Dict[str, List[str]]]: Return either a list of connection pair or a dictionary of lists which is collected by the specified option, source or sink.
    """

    if not os.path.exists(fileName):
        raise ValueError(f"The file {fileName} does not exist.")

    resultList = []
    with open(fileName, 'r') as f:
        file = f.read()
        file = re.sub(r"#.*", "", file)
    file = file.split("\n")
    for i, line in enumerate(file):
        line = line.replace(" ", "").replace("\t", "").split(",")
        line = [i for i in line if i != ""]
        if not line:
            continue
        if len(line) != 2:
            print(line)
            raise ValueError(
                f"Invalid list formatting in file: {fileName} at line {i}")
        left, right = line[0], line[1]

        leftList = []
        rightList = []
        _expandListPorts(left, leftList)
        _expandListPorts(right, rightList)
        resultList += list(zip(leftList, rightList))

    result = list(dict.fromkeys(resultList))
    resultDic = {}
    if collect == "source":
        for k, v in result:
            if k not in resultDic:
                resultDic[k] = []
            resultDic[k].append(v)
        return resultDic

    if collect == "sink":
        for k, v in result:
            for i in v:
                if i not in resultDic:
                    resultDic[i] = []
                resultDic[i].append(k)
        return resultDic

    return result


def _expandListPorts(port, PortList):
    """
    expand the .list file entry into list of tuple.
    """
    # a leading '[' tells us that we have to expand the list
    if "[" in port:
        if "]" not in port:
            raise ValueError(
                '\nError in function ExpandListPorts: cannot find closing ]\n')
        # port.find gives us the first occurrence index in a string
        left_index = port.find("[")
        right_index = port.find("]")
        before_left_index = port[0:left_index]
        # right_index is the position of the ']' so we need everything after that
        after_right_index = port[(right_index+1):]
        ExpandList = []
        ExpandList = re.split(r"\|", port[left_index+1:right_index])
        for entry in ExpandList:
            ExpandListItem = (before_left_index+entry+after_right_index)
            _expandListPorts(ExpandListItem, PortList)

    else:
        # print('DEBUG: else, just:',port)
        PortList.append(port)
    return


def parseFileVHDL(filename: str, belPrefix: str = "") -> Tuple[List[Tuple[str, IO]], List[Tuple[str, IO]], List[Tuple[str, IO]], List[Tuple[str, IO]], int, bool, Dict[str, int]]:
    """
    Parse a VHDL bel file and return all the related information of the bel. The tuple returned for relating to ports will
    be a list of (belName, IO) pair.

    For further example of bel mapping please look at parseFileVerilog

    Args:
        filename (str): The input file name.
        belPrefix (str, optional): The bel prefix provided by the CSV file. Defaults to "".

    Raises:
        ValueError: File not found
        ValueError: No permission to access the file
        ValueError: Cannot find the port section in the file which defines the bel ports.

    Returns:
        Tuple[List[Tuple[str, IO]], List[Tuple[str, IO]], List[Tuple[str, IO]], List[Tuple[str, IO]], int, bool, Dict[str, int]]:
        Bel internal ports, bel external ports, bel config ports, bel shared ports, number of configuration bit in the bel,
        whether the bel have UserCLK, and the bel config bit mapping.
    """
    internal: List[Tuple[str, IO]] = []
    external: List[Tuple[str, IO]] = []
    config: List[Tuple[str, IO]] = []
    shared: List[Tuple[str, IO]] = []
    carry: List[Tuple[str, IO]] = []
    isExternal = False
    isConfig = False
    isShared = False
    userClk = False
    isCarry = False

    try:
        with open(filename, "r") as f:
            file = f.read()
    except FileNotFoundError:
        print(f"File {filename} not found.")
        exit(-1)
    except PermissionError:
        print(f"Permission denied to file {filename}.")
        exit(-1)

    belMapDic = _belMapProcessing(file, filename, "vhdl")

    if result := re.search(r"NoConfigBits.*?=.*?(\d+)", file, re.IGNORECASE):
        noConfigBits = int(result.group(1))
    else:
        print(f"Cannot find NoConfigBits in {filename}")
        print("Assume the number of configBits is 0")
        noConfigBits = 0

    if len(belMapDic) != noConfigBits:
        raise ValueError(
            f"NoConfigBits does not match with the BEL map in file {filename}, length of BelMap is {len(belMapDic)}, but with {noConfigBits} config bits")

    portSection = ""
    if result := re.search(r"port.*?\((.*?)\);", file,
                           re.MULTILINE | re.DOTALL | re.IGNORECASE):
        portSection = result.group(1)
    else:
        raise ValueError(
            f"Could not find port section in file {filename}")

    preGlobal, postGlobal = portSection.split("-- GLOBAL")

    for line in preGlobal.split("\n"):
        if "IMPORTANT" in line:
            continue
        if "EXTERNAL" in line:
            isExternal = True
        if "CONFIG" in line:
            isConfig = True
        if "SHARED_PORT" in line:
            isShared = True
        if "CARRY" in line:
            isCarry = True

        line = re.sub(r"STD_LOGIC.*", "", line, flags=re.IGNORECASE)
        line = re.sub(r";.*", "", line, flags=re.IGNORECASE)
        line = re.sub(r"--*", "", line, flags=re.IGNORECASE)
        line = line.replace(" ", "").replace("\t", "").replace(";", "")
        result = re.search(r"(.*):(.*)", line)
        if not result:
            continue
        portName = f"{belPrefix}{result.group(1)}"
        direction = IO[result.group(2).upper()]

        if isExternal and not isShared:
            external.append((portName, direction))
        elif isConfig:
            config.append((portName, direction))
        elif isShared:
            # shared port do not have a prefix
            shared.append((portName.removeprefix(belPrefix),direction))
        else:
            internal.append((portName, direction))

            if isCarry:
                if direction is IO["INOUT"]:
                    raise ValueError(
                        f"CARRY can't be used with INOUT ports for port {portName}!")
                if not direction in carry:
                    carry[direction] = portName
                else:
                    raise ValueError(
                        f"{portName} can't be a carry {direction}, \
                        since {carry[direction]} already is!")

        if "UserCLK" in portName:
            userClk = True

        isExternal = False
        isConfig = False
        isShared = False
        isCarry = False

    result = re.search(
        r"NoConfigBits\s*:\s*integer\s*:=\s*(\w+)", file, re.IGNORECASE | re.DOTALL)
    if result:
        try:
            noConfigBits = int(result.group(1))
        except ValueError:
            print(f"NoConfigBits is not an integer: {result.group(1)}")
            print("Assume the number of configBits is 0")
            noConfigBits = 0
    else:
        print(f"Cannot find NoConfigBits in {filename}")
        print("Assume the number of configBits is 0")
        noConfigBits = 0

    return internal, external, config, shared, noConfigBits, userClk, belMapDic, carry


def parseFileVerilog(filename: str, belPrefix: str = "") -> Tuple[List[Tuple[str, IO]], List[Tuple[str, IO]], List[Tuple[str, IO]], List[Tuple[str, IO]], int, bool, Dict[str, Dict]]:
    """
    Parse a Verilog bel file and return all the related information of the bel. The tuple returned for relating to ports
    will be a list of (belName, IO) pair.

    The function will also parse and record all the FABulous attribute which all starts with ::

        (* FABulous, <type>, ... *)

    The <type> can be one the following:

    * **BelMap**
    * **EXTERNAL**
    * **SHARED_PORT**
    * **GLOBAL**
    * **CONFIG_PORT**

    The **BelMap** attribute will specify the bel mapping for the bel. This attribute should be placed before the start of
    the module The bel mapping is then used for generating the bitstream specification. Each of the entry in the attribute will have the following format::

    <name> = <value>

    ``<name>`` is the name of the feature and ``<value>`` will be the bit position of the feature. ie. ``INIT=0`` will specify that the feature ``INIT`` is located at bit 0.
    Since a single feature can be mapped to multiple bits, this is currently done by specifying multiple entries for the same feature. This will be changed in the future.
    The bit specification is done in the following way::

        INIT_a_1=1, INIT_a_2=2, ...

    The name of the feature will be converted to ``INIT_a[1]``, ``INIT_a[2]`` for the above example. This is necessary
    because  Verilog does not allow square brackets as part of the attribute name.

    **EXTERNAL** attribute will notify FABulous to put the pin in the top module during the fabric generation.

    **SHARED_PORT** attribute will notify FABulous this the pin is shared between multiple bels. Attribute need to go with
    the **EXTERNAL** attribute.

    **GLOBAL** attribute will notify FABulous to stop parsing any pin after this attribute.

    **CONFIG_PORT** attribute will notify FABulous the port is for configuration.

    Example:
        .. code-block :: verilog

            (* FABulous, BelMap,
            single_bit_feature=0, //single bit feature, single_bit_feature=0
            multiple_bits_0=1, //multiple bit feature bit0, multiple_bits[0]=1
            multiple_bits_1=2 //multiple bit feature bit1, multiple_bits[1]=2
            *)
            module exampleModule (externalPin, normalPin1, normalPin2, sharedPin, globalPin);
                (* FABulous, EXTERNAL *) input externalPin;
                input normalPin;
                (* FABulous, EXTERNAL, SHARED_PORT *) input sharedPin;
                (* FABulous, GLOBAL) input globalPin;
                output normalPin2; //do not get parsed
                ...

    Args:
        filename (str): The filename of the bel file.
        belPrefix (str, optional): The bel prefix provided by the CSV file. Defaults to "".

    Raises:
        ValueError: File not found
        ValueError: No permission to access the file

    Returns:
        Tuple[List[Tuple[str, IO]], List[Tuple[str, IO]], List[Tuple[str, IO]], List[Tuple[str, IO]], int, bool, Dict[str, Dict]]:
        Bel internal ports, bel external ports, bel config ports, bel shared ports, number of configuration bit in the bel,
        whether the bel have UserCLK, and the bel config bit mapping.
    """
    internal: List[Tuple[str, IO]] = []
    external: List[Tuple[str, IO]] = []
    config: List[Tuple[str, IO]] = []
    shared: List[Tuple[str, IO]] = []
    carry: Dict[IO, str] = {}
    isExternal = False
    isConfig = False
    isShared = False
    isCarry = False
    userClk = False
    noConfigBits = 0

    try:
        with open(filename, "r") as f:
            file = f.read()
    except FileNotFoundError:
        print(f"File {filename} not found.")
        exit(-1)
    except PermissionError:
        print(f"Permission denied to file {filename}.")
        exit(-1)

    belMapDic = _belMapProcessing(file, filename, "verilog")

    if result := re.search(r"NoConfigBits.*?=.*?(\d+)", file, re.IGNORECASE):
        noConfigBits = int(result.group(1))
    else:
        print(f"Cannot find NoConfigBits in {filename}")
        print("Assume the number of configBits is 0")
        noConfigBits = 0

    if len(belMapDic) != noConfigBits:
        raise ValueError(
            f"NoConfigBits does not match with the BEL map in file {filename}, length of BelMap is {len(belMapDic)}, but with {noConfigBits} config bits")

    file = file.split("\n")

    for line in file:
        if result := re.search(r".*(input|output|inout).*?(\w+);", line, re.IGNORECASE):
            cleanedLine = line.replace(" ", "")
            if attribute := re.search(r"\(\*FABulous,(.*)\*\)", cleanedLine):
                if "EXTERNAL" in attribute.group(1):
                    isExternal = True
                if "CONFIG" in attribute.group(1):
                    isConfig = True
                if "SHARED_PORT" in attribute.group(1):
                    isShared = True
                if "GLOBAL" in attribute.group(1):
                    break
                if "CARRY" in attribute.group(1):
                    isCarry = True

            portName = f"{belPrefix}{result.group(2)}"
            direction = IO[result.group(1).upper()]

            if isExternal and not isShared:
                external.append((portName, direction))
            elif isConfig:
                config.append((portName, direction))
            elif isShared:
                # shared port do not have a prefix
                shared.append((portName.removeprefix(belPrefix), direction))
            else:
                internal.append((portName, direction))

            if isCarry:
                if direction is IO["INOUT"]:
                    raise ValueError(
                        f"CARRY can't be used with INOUT ports for port {portName}!")
                if not direction in carry:
                    carry[direction] = portName
                else:
                    raise ValueError(
                        f"{portName} can't be a carry {direction}, \
                        since {carry[direction]} already is!")

            if "UserCLK" in portName:
                userClk = True

            isExternal = False
            isConfig = False
            isShared = False
            isCarry = False

    return internal, external, config, shared, noConfigBits, userClk, belMapDic, carry


def _belMapProcessing(file: str, filename: str, syntax: Literal["vhdl", "verilog"]) -> Dict:
    pre = ""
    if syntax == "vhdl":
        pre = "--.*?"

    belEnumsDic = {}
    if belEnums := re.findall(pre+r"\(\*.*?FABulous,.*?BelEnum,(.*?)\*\)", file, re.DOTALL | re.MULTILINE):
        for enums in belEnums:
            enums = enums.replace("\n", "").replace(" ", "").replace("\t", "")
            enums = enums.split(",")
            enums = [i for i in enums if i != "" and i != " "]
            if enumParse := re.search(r"(.*?)\[(\d+):(\d+)\]", enums[0]):
                name = enumParse.group(1)
                start = int(enumParse.group(2))
                end = int(enumParse.group(3))
            else:
                raise ValueError(
                    f"Invalid enum {enums[0]} in file {filename}")
            belEnumsDic[name] = {}
            for i in enums[1:]:
                key, value = i.split("=")
                belEnumsDic[name][key] = {}
                bitValue = list(value)
                if start > end:
                    for j in range(start, end - 1, -1):
                        belEnumsDic[name][key][j] = bitValue.pop(0)
                else:
                    for j in range(start, end + 1):
                        belEnumsDic[name][key][j] = bitValue.pop(0)

    belMapDic = {}
    if belMap := re.search(pre+r"\(\*.*FABulous,.*?BelMap,(.*?)\*\)", file, re.DOTALL | re.MULTILINE):
        belMap = belMap.group(1)
        belMap = belMap.replace("\n", "").replace(" ", "").replace("\t", "")
        belMap = belMap.split(",")
        belMap = [i for i in belMap if i != "" and i != " "]
        for bel in belMap:
            bel = bel.split("=")
            belNameTemp = bel[0].rsplit("_", 1)
            # process scalar
            if len(belNameTemp) > 1 and belNameTemp[1].isnumeric():
                bel[0] = f"{belNameTemp[0]}[{belNameTemp[1]}]"
            belMapDic[bel[0]] = {}
            if bel == ['']:
                continue
            # process enum data type
            if bel[0] in list(belEnumsDic.keys()):
                belMapDic[bel[0]] = belEnumsDic[bel[0]]
            # process vector input
            elif ":" in bel[1]:
                start, end = bel[1].split(":")
                start, end = int(start), int(end)
                if start > end:
                    length = start - end + 1
                    for i in range(2**length-1, -1, -1):
                        belMapDic[bel[0]][i] = {}
                        bitMap = list(f"{i:0{length.bit_length()}b}")
                        for v in range(len(bitMap)-1, -1, -1):
                            belMapDic[bel[0]][i][v] = bitMap.pop(0)
                else:
                    length = end - start + 1
                    for i in range(0, 2**length):
                        belMapDic[bel[0]][i] = {}
                        bitMap = list(
                            f"{2**length-i-1:0{length.bit_length()}b}")
                        for v in range(len(bitMap)-1, -1, -1):
                            belMapDic[bel[0]][i][v] = bitMap.pop(0)
            else:
                belMapDic[bel[0]][0] = {0: '1'}
    return belMapDic


def parseMatrix(fileName: str, tileName: str) -> Dict[str, List[str]]:
    """
    parse the matrix csv into a dictionary from destination to source

    Args:
        fileName (str): directory of the matrix csv file
        tileName (str): name of the tile need to be parsed

    Raises:
        ValueError: Non matching matrix file content and tile name

    Returns:
        Dict[str, List[str]]: dictionary from destination to a list of source
    """

    connectionsDic = {}
    with open(fileName, 'r') as f:
        file = f.read()
        file = re.sub(r"#.*", "", file)
        file = file.split("\n")

    if file[0].split(",")[0] != tileName:
        print(fileName)
        print(file[0].split(","))
        print(tileName)
        raise ValueError(
            'Tile name (top left element) in csv file does not match tile name in tile object')

    destList = file[0].split(",")[1:]

    for i in file[1:]:
        i = i.split(",")
        portName, connections = i[0], i[1:]
        if portName == "":
            continue
        indices = [k for k, v in enumerate(connections) if v == "1"]
        connectionsDic[portName] = [destList[j] for j in indices]
    return connectionsDic


def parseConfigMem(fileName: str, maxFramePerCol: int, frameBitPerRow: int, globalConfigBits: int) -> List[ConfigMem]:
    """
    Parse the config memory csv file into a list of ConfigMem objects

    Args:
        fileName (str): directory of the config memory csv file
        maxFramePerCol (int): maximum number of frames per column
        frameBitPerRow (int): number of bits per row
        globalConfigBits (int): number of global config bits for the config memory

    Raises:
        ValueError: Invalid amount of frame entries in the config memory csv file
        ValueError: Too many value in bit mask
        ValueError: Length of bit mask does not match with the number of frame bits per row
        ValueError: Bit mast does not have enough value matching the number of the given config bits
        ValueError: repeated config bit entry in ':' separated format in config bit range
        ValueError: repeated config bit entry in list format in config bit range
        ValueError: Invalid range entry in config bit range

    Returns:
        List[ConfigMem]: _description_
    """
    with open(fileName) as f:
        mappingFile = list(csv.DictReader(f))

        # remove the pretty print from used_bits_mask
        for i, _ in enumerate(mappingFile):
            mappingFile[i]["used_bits_mask"] = mappingFile[i]["used_bits_mask"].replace(
                "_", "")

        # we should have as many lines as we have frames (=framePerCol)
        if len(mappingFile) != maxFramePerCol:
            raise ValueError(
                f"WARNING: the bitstream mapping file has only {len(mappingFile)} entries but MaxFramesPerCol is {maxFramePerCol}")

        # we also check used_bits_mask (is a vector that is as long as a frame and contains a '1' for a bit used and a '0' if not used (padded)
        usedBitsCounter = 0
        for entry in mappingFile:
            if entry["used_bits_mask"].count("1") > frameBitPerRow:
                raise ValueError(
                    f"bitstream mapping file {fileName} has to many 1-elements in bitmask for frame : {entry['frame_name']}")
            if len(entry["used_bits_mask"]) != frameBitPerRow:
                raise ValueError(
                    f"bitstream mapping file {fileName} has has a too long or short bitmask for frame : {entry['frame_name']}")
            usedBitsCounter += entry["used_bits_mask"].count("1")

        if usedBitsCounter != globalConfigBits:
            raise ValueError(
                f"bitstream mapping file {fileName} has a bitmask miss match; bitmask has in total {usedBitsCounter} 1-values for {globalConfigBits} bits")

        allConfigBitsOrder = []
        configMemEntry = []
        for entry in mappingFile:
            configBitsOrder = []
            entry["ConfigBits_ranges"] = entry["ConfigBits_ranges"].replace(
                " ", "").replace("\t", "")

            if ":" in entry["ConfigBits_ranges"]:
                left, right = re.split(':', entry["ConfigBits_ranges"])
                # check the order of the number, if right is smaller than left, then we swap them
                left, right = int(left), int(right)
                if right < left:
                    left, right = right, left
                    numList = list(reversed(range(left, right + 1)))
                else:
                    numList = list(range(left, right + 1))

                for i in numList:
                    if i in allConfigBitsOrder:
                        raise ValueError(
                            f"Configuration bit index {i} already allocated in {fileName}, {entry['frame_name']}")
                    configBitsOrder.append(i)

            elif ";" in entry["ConfigBits_ranges"]:
                for item in entry["ConfigBits_ranges"].split(";"):
                    if int(item) in allConfigBitsOrder:
                        raise ValueError(
                            f"Configuration bit index {item} already allocated in {fileName}, {entry['frame_name']}")
                    configBitsOrder.append(int(item))

            elif "NULL" in entry["ConfigBits_ranges"]:
                continue

            else:
                raise ValueError(
                    f"Range {entry['ConfigBits_ranges']} is not a valid format. It should be in the form [int]:[int] or [int]. If there are multiple ranges it should be separated by ';'")

            allConfigBitsOrder += configBitsOrder

            if entry["used_bits_mask"].count("1") > 0:
                configMemEntry.append(ConfigMem(frameName=entry["frame_name"],
                                                frameIndex=int(
                                                    entry["frame_index"]),
                                                bitsUsedInFrame=entry["used_bits_mask"].count(
                                                    "1"),
                                                usedBitMask=entry["used_bits_mask"],
                                                configBitRanges=configBitsOrder))

    return configMemEntry


def generateSwitchmatrixList(tileName: str, bels: List[Bel], outFile: str, carryportsTile: Dict[IO, str]) -> int:
    """
    Generate a swichtmatrix listfile.
    """
    # TODO: fix outfile name --> should now be tile.filepath
    filePath = os.path.dirname(outFile)
    CLBDummyFile = f"{filePath}/../CLB_DUMMY/CLB_DUMMY_switchmatrix.list"

    with open(CLBDummyFile, 'r') as f:
        file = f.read()
        #  file = re.sub(r"#.*", "", file)

    #TODO remove carry ports from bel.ports
    belIns = []
    belOuts = []
    belCarrys = []

    belIns = sum((bel.inputs for bel in bels), [])
    belOuts = sum((bel.outputs for bel in bels), [])
    belCarrys += (bel.carry for bel in bels)

    carryports: Dict[IO, List[str]] = {}
    carryports[IO.INPUT] = []
    carryports[IO.OUTPUT] = []

    for carry in belCarrys:
        print(carry[IO.INPUT])
        carryports[IO.INPUT].append(carry[IO.INPUT])
        belIns.remove(carry[IO.INPUT])
        print(carry[IO.OUTPUT])
        carryports[IO.OUTPUT].append(carry[IO.OUTPUT])
        belOuts.remove(carry[IO.OUTPUT])

    if len(belIns) > 32:
        raise ValueError(
            f"Tile {tileName} has {len(belIns)} Bel inputs, switchmatrix gen can only handle 32 inputs"
        )

    if len(belOuts) > 8:
        raise ValueError(
            f"Tile {tileName} has {len(belOuts)} Bel outputs, switchmatrix gen can only handle 8 outputs"
        )

    ## Copied from fileparser -> parseList()
    ## Converts listfile to portpairs
    ## TODO cleanup
    file = re.sub(r"#.*", "", file)
    file = file.split("\n")
    resultList = []
    for i, line in enumerate(file):
        line = line.replace(" ", "").replace("\t", "").split(",")
        line = [i for i in line if i != ""]
        if not line:
            continue
        if len(line) != 2:
            print(line)
            raise ValueError(
                f"Invalid list formatting in file: {line}")
        left, right = line[0], line[1]

        leftList = []
        rightList = []
        _expandListPorts(left, leftList)
        _expandListPorts(right, rightList)
        resultList += list(zip(leftList, rightList))

    # build a dict, with the old names from the list file and the replacement from the bels
    replaceDic = {}
    for i, port in enumerate(belIns):
        replaceDic[f"CLB{math.floor(i/4)}_I{i%4}"] = f"{port}"
    for i, port in enumerate(belOuts):
        replaceDic[f"CLB{i%8}_O"] = f"{port}"

    # generate a list of sinks, with their connection count, if they have at least 5 connections
    sinks_num = [sink for _, sink in resultList]
    sinks_num = {i:sinks_num.count(i) for i in sinks_num if sinks_num.count(i) > 4}

    connections = {}
    for source, sink in resultList:
        # replace the old names with the new ones
        if source in replaceDic:
            source = replaceDic[source]
        if sink in replaceDic:
            sink = replaceDic[sink]
        if "CLB" in source:
            # drop the whole multiplexer, if its not connected
            continue
        if "CLB" in sink:
            # replace sink with the sink with the lowest connection count
            sink = min(sinks_num, key=sinks_num.get)
            sinks_num[sink] = sinks_num[sink] + 1

        if source not in connections:
            connections[source] = []
        connections[source].append(sink)

    # generate listfile strings
    configBit = 0
    listfile = []
    for source, sinks in connections.items():
        muxsize = len(sinks)
        if muxsize%2 != 0 and muxsize > 1:
            logger.warning(f"For source {source} mux size is {len(sinks)} with sinks: {sinks}")
            listfile.append(f"# WARNING: Muxsize {muxsize} for source {source}")

        if muxsize == 1:
            listfile.append(f"{source},{sinks[0]}")
        else: # generate a line for listfile
            configBit += muxsize.bit_length()-1
            #  listfile.append(f"# Muxsize {muxsize} for source {source}")
            ltmp = f"[{source}"
            rtmp = f"[{sinks[0]}"
            for sink in sinks[1:]:
                ltmp += f"|{source}"
                rtmp += f"|{sink}"
            rtmp += "]"
            ltmp += "]"
            listfile.append(f"{ltmp},{rtmp}")

    #TODO: Add support for multi Carrychains, unroll fabric csv wire input
    if carryports and carryportsTile:
        #  breakpoint()
        # append Tile carry in to beginning of output list, since it should be connected to the first bel carry input
        carryports[IO.OUTPUT].insert(0, carryportsTile[IO.INPUT])
        # append Tile carry out to the end of output list, since it should be connected to the last bel carry out
        carryports[IO.INPUT].append(carryportsTile[IO.OUTPUT])

        if len(carryports[IO.INPUT]) is not len(carryports[IO.OUTPUT]):
            raise ValueError(f"Carryports missmatch! \
                             There are {len(carryports[IO.INPUT])} INPUTS \
                             and {len(carryports[IO.OUTPUT])} outputs!")

        listfile.append("# Connect carrychain")
        for cin, cout in zip(carryports[IO.INPUT], carryports[IO.OUTPUT]):
            listfile.append(f"{cin},{cout}")

    f = open(outFile, "w")
    f.write("\n".join(str(line) for line in listfile))
    f.close()

    return configBit


if __name__ == '__main__':
    # result = parseFabricCSV('fabric.csv')
    # result1 = parseList('RegFile_switch_matrix.list', collect="source")
    # result = parseFileVerilog('./LUT4c_frame_config_dffesr.v')

    result2 = parseFileVerilog("./test.txt")
    # print(result[0])
    # print(result[1])
    # print(result[2])
    # print(result[3])

    # print(result.tileDic["W_IO"].portsInfo)
