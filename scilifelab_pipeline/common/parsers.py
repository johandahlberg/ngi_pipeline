## TODO this is a lot of code I haven't read and some of which can doubtless be removed and some of which may be missing things

import collections
import os
import re
## TODO do we need these xml thingers? what about xmltodict instead?
import xml.etree.cElementTree as ET
import xml.parsers.expat

from scilifelab_pipeline.log import minimal_logger
LOG = minimal_logger(__name__)

class MetricsParser():
    """Basic class for parsing metrics"""
    def __init__(self, log=None):
        self.log = LOG
        if log:
            self.log = log

    def parse_bc_metrics(self, in_handle):
        data = {}
        while 1:
            line = in_handle.readline()
            if not line:
                break
            vals = line.rstrip("\t\n\r").split("\t")
            data[vals[0]] = int(vals[1])
        return data

    def parse_filter_metrics(self, in_handle):
        data = {}
        data["reads"] = int(in_handle.readline().rstrip("\n").split(" ")[-1])
        data["reads_aligned"] = int(in_handle.readline().split(" ")[-2])
        data["reads_fail_align"] = int(in_handle.readline().split(" ")[-2])
        return data

    def parse_fastq_screen_metrics(self, in_handle):
        in_handle.readline()
        data = {}
        while 1:
            line = in_handle.readline()
            if not line:
                break
            vals = line.rstrip("\t\n").split("\t")
            data[vals[0]] = {}
            data[vals[0]]["Unmapped"] = float(vals[1])
            data[vals[0]]["Mapped_One_Library"] = float(vals[2])
            data[vals[0]]["Mapped_Multiple_Libraries"] = float(vals[3])
        return data

    def parse_undemultiplexed_barcode_metrics(self, in_handle):

        data = collections.defaultdict(list)
        for line in in_handle:
            data[line['lane']].append({c:[line[c],''][line[c] is None] for c in in_handle.fieldnames if c != 'lane'})
        return data

    def parse_bcbb_checkpoints(self, in_handle):

        TIMEFORMAT = "%Y-%m-%dT%H:%M:%S.%f"
        timestamp = []
        for line in in_handle:
            try:
                ts = "{}Z".format(datetime.datetime.strptime(line.strip(), TIMEFORMAT).isoformat())
                timestamp.append(ts)
            except ValueError:
                pass

        return timestamp

    def parse_software_versions(self, in_handle):
        sver = {}
        for line in in_handle:
            try:
                s = line.split()
                if len(s) == 2:
                    sver[s[0]] = s[1]
            except:
                pass
        return sver


# Generic XML to dict parsing
# See http://code.activestate.com/recipes/410469-xml-as-dictionary/
class XmlToList(list):
    def __init__(self, aList):
        for element in aList:
            if element:
                # treat like dict
                if len(element) == 1 or element[0].tag != element[1].tag:
                    self.append(XmlToDict(element))
                # treat like list
                elif element[0].tag == element[1].tag:
                    self.append(XmlToList(element))
            elif element.text:
                text = element.text.strip()
                if text:
                    self.append(text)
            else:
                # Set dict for attributes
                self.append({k:v for k,v in element.items()})

class XmlToDict(dict):
    '''
    Example usage:

    >>> tree = ET.parse('your_file.xml')
    >>> root = tree.getroot()
    >>> xmldict = XmlToDict(root)

    Or, if you want to use an XML string:

    >>> root = ET.XML(xml_string)
    >>> xmldict = XmlToDict(root)

    And then use xmldict for what it is... a dict.
    '''
    def __init__(self, parent_element):
        if parent_element.items():
            self.update(dict(parent_element.items()))
        for element in parent_element:
            if element:
                # treat like dict - we assume that if the first two tags
                # in a series are different, then they are all different.
                if len(element) == 1 or element[0].tag != element[1].tag:
                    aDict = XmlToDict(element)
                # treat like list - we assume that if the first two tags
                # in a series are the same, then the rest are the same.
                else:
                    # here, we put the list in dictionary; the key is the
                    # tag name the list elements all share in common, and
                    # the value is the list itself
                    aDict = {element[0].tag: XmlToList(element)}
                # if the tag has attributes, add those to the dict
                if element.items():
                    aDict.update(dict(element.items()))
                self.update({element.tag: aDict})
            # this assumes that if you've got an attribute in a tag,
            # you won't be having any text. This may or may not be a
            # good idea -- time will tell. It works for the way we are
            # currently doing XML configuration files...
            elif element.items():
                self.update({element.tag: dict(element.items())})
                # add the following line
                self[element.tag].update({"__Content__":element.text})

            # finally, if there are no child tags and no attributes, extract
            # the text
            else:
                self.update({element.tag: element.text})


class RunMetricsParser(dict):
    """Generic Run Parser class"""
    _metrics = []
    ## Following paths are ignored
    ignore = "|".join(["tmp", "tx", "-split", "log"])
    reignore = re.compile(ignore)

    def __init__(self, log=None):
        super(RunMetricsParser, self).__init__()
        self.files = []
        self.path=None
        self.log = LOG
        if log:
            self.log = log

    def _collect_files(self):
        if not self.path:
            return
        if not os.path.exists(self.path):
            raise IOError
        self.files = []
        for root, dirs, files in os.walk(self.path):
            if re.search(self.reignore, root):
                continue
            self.files = self.files + [os.path.join(root, x) for x in files]

    def filter_files(self, pattern, filter_fn=None):
        """Take file list and return those files that pass the filter_fn criterium"""
        def filter_function(f):
            return re.search(pattern, f) != None
        if not filter_fn:
            filter_fn = filter_function
        return filter(filter_fn, self.files)

    def parse_json_files(self, filter_fn=None):
        """Parse json files and return the corresponding dicts
        """
        def filter_function(f):
            return f is not None and f.endswith(".json")
        if not filter_fn:
            filter_fn = filter_function
        files = self.filter_files(None,filter_fn)
        dicts = []
        for f in files:
            with open(f) as fh:
                dicts.append(json.load(fh))
        return dicts

    def parse_csv_files(self, filter_fn=None):
        """Parse csv files and return a dict with filename as key and the corresponding dicts as value
        """
        def filter_function(f):
            return f is not None and f.endswith(".csv")
        if not filter_fn:
            filter_fn = filter_function
        files = self.filter_files(None,filter_fn)
        dicts = {}
        for f in files:
            with open(f) as fh:
                dicts[f] = [r for r in csv.DictReader(fh)]
        return dicts


class RunInfoParser():
    """RunInfo parser"""
    def __init__(self):
        self._data = {}
        self._element = None

    def parse(self, fp):
        self._parse_RunInfo(fp)
        return self._data

    def _start_element(self, name, attrs):
        self._element=name
        if name == "Run":
            self._data["Id"] = attrs["Id"]
            self._data["Number"] = attrs["Number"]
        elif name == "FlowcellLayout":
            self._data["FlowcellLayout"] = attrs
        elif name == "Read":
            self._data["Reads"].append(attrs)

    def _end_element(self, name):
        self._element=None

    def _char_data(self, data):
        want_elements = ["Flowcell", "Instrument", "Date"]
        if self._element in want_elements:
            self._data[self._element] = data
        if self._element == "Reads":
            self._data["Reads"] = []

    def _parse_RunInfo(self, fp):
        p = xml.parsers.expat.ParserCreate()
        p.StartElementHandler = self._start_element
        p.EndElementHandler = self._end_element
        p.CharacterDataHandler = self._char_data
        p.ParseFile(fp)


class RunParametersParser():
    """runParameters.xml parser"""
    def __init__(self):
        self.data = {}

    def parse(self, fh):

        tree = ET.parse(fh)
        root = tree.getroot()
        self.data = XmlToDict(root)
        # If not a MiSeq run, return the contents of the Setup tag
        if 'MCSVersion' not in self.data:
            self.data = self.data['Setup']
        return self.data


class FlowcellRunMetricsParser(RunMetricsParser):
    """Flowcell level class for parsing flowcell run metrics data."""
    _lanes = range(1,9)
    def __init__(self, path):
        RunMetricsParser.__init__(self)
        self.path = path
        self._collect_files()

    def parseRunInfo(self, fn="RunInfo.xml", **kw):
        infile = os.path.join(os.path.abspath(self.path), fn)
        self.log.debug("parseRunInfo: going to read {}".format(infile))
        if not os.path.exists(infile):
            self.log.warn("No such file {}".format(infile))
            return {}
        ## TODO revert this try-except later but try to restrict the except a bit
        #try:
        fp = open(infile)
        parser = RunInfoParser()
        data = parser.parse(fp)
        fp.close()
        return data
        #except:
        #    self.log.warn("Reading file {} failed".format(os.path.join(os.path.abspath(self.path), fn)))
        #    return {}

    def parseRunParameters(self, fn="runParameters.xml", **kw):
        """Parse runParameters.xml from an Illumina run.

        :param fn: filename
        :param **kw: keyword argument

        :returns: parsed data structure
        """
        infile = os.path.join(os.path.abspath(self.path), fn)
        self.log.debug("parseRunParameters: going to read {}".format(infile))
        if not os.path.exists(infile):
            self.log.warn("No such files {}".format(infile))
            return {}
        ## TODO revert this try-except later but try to restrict the except a bit
        #try:
        with open(infile) as fh:
            parser = RunParametersParser()
            data = parser.parse(fh)
        return data
        #except:
        #    self.log.warn("Reading file {} failed".format(os.path.join(os.path.abspath(self.path), fn)))
        #    return {}

    def parseDemultiplexConfig(self, fn="DemultiplexConfig.xml", **kw):
        """Parse the DemultiplexConfig.xml configuration files"""
        pattern = os.path.join(os.path.abspath(self.path), "Unaligned*", fn)
        cfg = {}
        for cfgfile in glob.glob(pattern):
            parser = DemultiplexConfigParser(cfgfile)
            data = parser.parse()
            if len(data) > 0:
                cfg[os.path.basename(os.path.dirname(cfgfile))] = data
        return cfg

    def parse_samplesheet_csv(self, runinfo_csv="SampleSheet.csv", **kw):
        infile = os.path.join(os.path.abspath(self.path), runinfo_csv)
        self.log.debug("parse_samplesheet_csv: going to read {}".format(infile))
        if not os.path.exists(infile):
            self.log.warn("No such file {}".format(infile))
            return {}
        try:
            fp = open(infile)
            runinfo = [x for x in csv.DictReader(fp)]
            fp.close()
            return runinfo
        except:
            self.log.warn("Reading file {} failed".format(infile))
            return {}

    def parse_run_info_yaml(self, run_info_yaml="run_info.yaml", **kw):
        infile = os.path.join(os.path.abspath(self.path), run_info_yaml)
        self.log.debug("parse_run_info_yaml: going to read {}".format(infile))
        if not os.path.exists(infile):
            self.log.warn("No such file {}".format(infile))
            return {}
        try:
            fp = open(infile)
            runinfo = yaml.load(fp)
            fp.close()
            return runinfo
            return True
        except:
            self.log.warn("No such file {}".format(infile))
            return False

    def parse_illumina_metrics(self, fullRTA=False, **kw):
        self.log.debug("parse_illumina_metrics")
        fn = []
        for root, dirs, files in os.walk(os.path.abspath(self.path)):
            for f in files:
                if f.endswith(".xml"):
                    fn.append(os.path.join(root, f))
        self.log.debug("Found {} RTA files {}...".format(len(fn), ",".join(fn[0:10])))
        parser = IlluminaXMLParser()
        metrics = parser.parse(fn, fullRTA)
        def filter_function(f):
            return f is not None and f == "run_summary.json"
        try:
            metrics.update(self.parse_json_files(filter_fn=filter_function).pop(0))
        except IndexError:
            pass
        return metrics

    def parse_filter_metrics(self, fc_name, **kw):
        """pre-CASAVA: Parse filter metrics at flowcell level"""
        self.log.debug("parse_filter_metrics for flowcell {}".format(fc_name))
        lanes = {str(k):{} for k in self._lanes}
        for lane in self._lanes:
            pattern = "{}_[0-9]+_[0-9A-Za-z]+(_nophix)?.filter_metrics".format(lane)
            lanes[str(lane)]["filter_metrics"] = {"reads":None, "reads_aligned":None, "reads_fail_align":None}
            files = self.filter_files(pattern)
            self.log.debug("filter metrics files {}".format(",".join(files)))
            try:
                fp = open(files[0])
                parser = MetricsParser()
                data = parser.parse_filter_metrics(fp)
                fp.close()
                lanes[str(lane)]["filter_metrics"] = data
            except:
                self.log.warn("No filter nophix metrics for lane {}".format(lane))
        return lanes

    def parse_bc_metrics(self, fc_name, **kw):
        """Parse bc metrics at sample level"""
        self.log.debug("parse_bc_metrics for flowcell {}".format(fc_name))
        lanes = {str(k):{} for k in self._lanes}
        for lane in self._lanes:
            pattern = "{}_[0-9]+_[0-9A-Za-z]+(_nophix)?[\._]bc[\._]metrics".format(lane)
            lanes[str(lane)]["bc_metrics"] = {}
            files = self.filter_files(pattern)
            self.log.debug("bc metrics files {}".format(",".join(files)))
            try:
                parser = MetricsParser()
                fp = open(files[0])
                data = parser.parse_bc_metrics(fp)
                fp.close()
                lanes[str(lane)]["bc_metrics"] = data
            except:
                self.log.warn("No bc_metrics info for lane {}".format(lane))
        return lanes

    def parse_undemultiplexed_barcode_metrics(self, fc_name, **kw):
        """Parse the undetermined indices top barcodes materics
        """

        # Use a glob to allow for multiple fastq folders
        metrics_file_pattern = os.path.join(self.path, "Unaligned*", "Basecall_Stats_*{}".format(fc_name[1:]), "Undemultiplexed_stats.metrics")
        metrics = {'undemultiplexed_barcodes': []}
        for metrics_file in glob.glob(metrics_file_pattern):
            self.log.debug("parsing {}".format(metrics_file))
            if not os.path.exists(metrics_file):
                self.log.warn("No such file {}".format(metrics_file))
                continue

            with open(metrics_file) as fh:
                parser = MetricsParser()
                in_handle = csv.DictReader(fh, dialect=csv.excel_tab)
                data = parser.parse_undemultiplexed_barcode_metrics(in_handle)
                for lane, items in data.items():
                    for item in items:
                        item['lane'] = lane
                        metrics['undemultiplexed_barcodes'].append(item)

        # Define a function for sorting values according to lane and yield
        def by_lane_yield(data):
            return '{}-{}'.format(data.get('lane',''),data.get('count','').zfill(10))

        # Remove duplicate entries resulting from multiple stats files
        for metric in ['undemultiplexed_barcodes']:
            dedupped = {}
            for row in metrics[metric]:
                key = "\t".join(row.values())
                if key not in dedupped:
                    dedupped[key] = row
                else:
                    self.log.warn("Duplicates of Undemultiplexed barcode entries discarded: {}".format(key[0:min(35,len(key))]))

            # Reformat the structure of the data to fit the downstream processing
            lanes = {}
            for row in sorted(dedupped.values(), key=by_lane_yield, reverse=True):
                lane = row['lane']
                if lane not in lanes:
                    lanes[lane] = {metric: {k:[] for k in row.keys()}}
                for k in row.keys():
                    lanes[lane][metric][k].append(row[k])

        return lanes

    def parse_demultiplex_stats_htm(self, fc_name, **kw):
        """Parse the Unaligned*/Basecall_Stats_*/Demultiplex_Stats.htm file
        generated from CASAVA demultiplexing and returns barcode metrics.
        """
        metrics = {"Barcode_lane_statistics": [],
                   "Sample_information": []}
        # Use a glob to allow for multiple fastq directories
        htm_file_pattern = os.path.join(self.path, "Unaligned*", "Basecall_Stats_*{}".format(fc_name[1:]), "Demultiplex_Stats.htm")
        for htm_file in glob.glob(htm_file_pattern):
            self.log.debug("parsing {}".format(htm_file))
            if not os.path.exists(htm_file):
                self.log.warn("No such file {}".format(htm_file))
                continue
            with open(htm_file) as fh:
                htm_doc = fh.read()
            soup = BeautifulSoup(htm_doc)
            ##
            ## Find headers
            allrows = soup.findAll("tr")
            column_gen=(row.findAll("th") for row in allrows)
            parse_row = lambda row: row
            headers = [h for h in map(parse_row, column_gen) if h]
            bc_header = [str(x.string) for x in headers[0]]
            smp_header = [str(x.string) for x in headers[1]]
            ## 'Known' headers from a Demultiplex_Stats.htm document
            bc_header_known = ['Lane', 'Sample ID', 'Sample Ref', 'Index', 'Description', 'Control', 'Project', 'Yield (Mbases)', '% PF', '# Reads', '% of raw clusters per lane', '% Perfect Index Reads', '% One Mismatch Reads (Index)', '% of >= Q30 Bases (PF)', 'Mean Quality Score (PF)']
            smp_header_known = ['None', 'Recipe', 'Operator', 'Directory']
            if not bc_header == bc_header_known:
                self.log.warn("Barcode lane statistics header information has changed. New format?\nOld format: {}\nSaw: {}".format(",".join((["'{}'".format(x) for x in bc_header_known])), ",".join(["'{}'".format(x) for x in bc_header])))
            if not smp_header == smp_header_known:
                self.log.warn("Sample header information has changed. New format?\nOld format: {}\nSaw: {}".format(",".join((["'{}'".format(x) for x in smp_header_known])), ",".join(["'{}'".format(x) for x in smp_header])))
            ## Fix first header name in smp_header since htm document is mal-formatted: <th>Sample<p></p>ID</th>
            smp_header[0] = "Sample ID"

            ## Parse Barcode lane statistics
            soup = BeautifulSoup(htm_doc)
            table = soup.findAll("table")[1]
            rows = table.findAll("tr")
            column_gen = (row.findAll("td") for row in rows)
            parse_row = lambda row: {bc_header[i]:str(row[i].string) for i in range(0, len(bc_header)) if row}
            metrics["Barcode_lane_statistics"].extend(map(parse_row, column_gen))

            ## Parse Sample information
            soup = BeautifulSoup(htm_doc)
            table = soup.findAll("table")[3]
            rows = table.findAll("tr")
            column_gen = (row.findAll("td") for row in rows)
            parse_row = lambda row: {smp_header[i]:str(row[i].string) for i in range(0, len(smp_header)) if row}
            metrics["Sample_information"].extend(map(parse_row, column_gen))

        # Define a function for sorting the values
        def by_lane_sample(data):
            return "{}-{}-{}".format(data.get('Lane',''),data.get('Sample ID',''),data.get('Index',''))

        # Post-process the metrics data to eliminate duplicates resulting from multiple stats files
        for metric in ['Barcode_lane_statistics', 'Sample_information']:
            dedupped = {}
            for row in metrics[metric]:
                key = "\t".join(row.values())
                if key not in dedupped:
                    dedupped[key] = row
                else:
                    self.log.debug("Duplicates of Demultiplex Stats entries discarded: {}".format(key[0:min(35,len(key))]))
            metrics[metric] = sorted(dedupped.values(), key=by_lane_sample)

        ## Set data
        return metrics