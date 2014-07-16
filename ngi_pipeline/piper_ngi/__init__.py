"""The Piper automated launcher script."""
from __future__ import print_function

import collections
import os
import re
import shlex
import shutil
import subprocess
import time

from ngi_pipeline.piper_ngi import workflows
from ngi_pipeline.log import minimal_logger
from ngi_pipeline.utils.filesystem import safe_makedir
from ngi_pipeline.utils import load_modules, execute_command_line
from ngi_pipeline.utils.config import load_xml_config, load_yaml_config
from ngi_pipeline.utils.parsers import parse_lane_from_filename, find_fastq_read_pairs_from_dir, \
                                get_flowcell_id_from_dirtree

LOG = minimal_logger(__name__)

def main(projects_to_analyze, config_file_path):
    """The main method.

    :param list flowcell_dirs_to_analyze: A lst of flowcell directories containing fastq files to analyze.
    :param str config_file_path: The path to the configuration file.
    """
    config = load_yaml_config(config_file_path)
    ## Problem with module system java version loading at the moment
    modules_to_load = ["java/sun_jdk1.7.0_25", "R/2.15.0"]
    ## Possibly the Java error could be non-fatal if it's available on PATH
    # Valid only for this session
    load_modules(modules_to_load)
    for project in projects_to_analyze:
        try:
            ## NOTE report.xml is created by sthlm2UUSNP at the moment, unsure in the long run
            #create_report_tsv(project)
            # Temporary until the file format switch
            convert_sthlm_to_uppsala(project)
            build_setup_xml(project, config)
            build_piper_cl(project, config)
            launch_piper_jobs(project)
        except Exception as e:
            error_msg = "Processing project {} failed:: {}".format(project, e.__repr__())
            LOG.error(error_msg)
            ## NOTE Or raise exception back to caller?
            #continue
            raise
    ## TODO Need to write workflow status to database under relevant heading!


def symlink_convert_file_names(project):
    """Converts standard Illumina (and Uppsala) file-naming format to the
    Stockholm format; required atm so sthlm2UUSNP can switch them back.
    """
    # A new directory must be created as sthlm2UUSNP chokes on unexpected files/names
    sthlm_dirname = "{}_sthlm".format(project.dirname)
    safe_makedir(os.path.join(project.base_path, sthlm_dirname))

    for sample in project:
        safe_makedir(os.path.join(project.base_path, sthlm_dirname, sample.dirname))
        for fcid in sample:
            safe_makedir(os.path.join(project.base_path, sthlm_dirname, sample.dirname, fcid.dirname))
            for fastq in fcid:
                m = re.match(r'(?P<sample_name>\w+)_(?P<index>[\w-]+)_L\d{2}(?P<lane_num>\d)_R(?P<read_num>\d)_.*(?P<ext>fastq.*)', fastq)
                try:
                    args_dict = m.groupdict()
                except AttributeError:
                    # No match
                    LOG.error("Filename \"{}\" did not match template! Blame {}".format(fastq, "Mario"))
                    continue
                args_dict.update({"date_fcid": fcid.name})
                scilifelab_named_file = "{lane_num}_{date_fcid}_{sample_name}_{read_num}.{ext}".format(**args_dict)
                fcid_src_path = os.path.join(project.base_path,
                                             project.dirname,
                                             sample.dirname,
                                             fcid.dirname,)
                fcid_dst_path = os.path.join(project.base_path,
                                             sthlm_dirname,
                                             sample.dirname,
                                             fcid.dirname,)
                src_fastq = os.path.join(fcid_src_path, fastq)
                dst_fastq = os.path.join(fcid_dst_path, scilifelab_named_file)
                try:
                    os.symlink(src_fastq, dst_fastq)
                except OSError as e:
                    if e.errno == 17:   # File already exists
                        pass
                    else:
                        raise
    ## NOTE These should not necessarily by the same but in practice they have been so far
    ##      and so the code treats them that way which is not ideal
    project.dirname = sthlm_dirname
    project.name = sthlm_dirname

def convert_sthlm_to_uppsala(project):
    """Convert projects from Stockholm style (three-level) to Uppsala style
    (two-level) using the sthlm2UUSNP Java utility.

    :param NGIProject project: The project to be converted.

    :returns: A list of projects with Uppsala-style directories as attributes.
    :rtype: list
    """
    # Need to convert file names from Illumina --> Sthlm format
    # so we can convert file names from Sthlm --> Illumina format
    symlink_convert_file_names(project)
    # Requires sthlm2UUSNP on PATH
    cl_template = "sthlm2UUSNP -i {input_dir} -o {output_dir}"
    LOG.info("Converting Sthlm project {} to UUSNP format".format(project))
    input_dir = os.path.join(project.base_path, project.dirname)
    uppsala_dirname = "{}_UUSNP".format(project.dirname)
    output_dir = os.path.join(project.base_path, uppsala_dirname)
    com = cl_template.format(input_dir=input_dir, output_dir=output_dir)
    try:
        subprocess.check_call(shlex.split(com))
    except subprocess.CalledProcessError as e:
        # Fails most commonly if a file/directory already exists. Should it?
        error_msg = ("Unable to convert Sthlm->UU format for "
                     "project {}: {}".format(project, e))
        LOG.error(error_msg)
        raise RuntimeError(error_msg)
    ## NOTE sthlm2UUSNP automatically creates the needed report.xml files,
    ##      so for the moment we don't need to copy anything
    #for ext in ["tsv", "xml"]:
    #    report_src_file = os.path.join(project.base_path, project.dirname, "report.{}".format(ext))
    #    if os.path.isfile(report_src_file):
    #        report_dst_file = os.path.join(project.base_path, uppsala_dirname, "report.{}".format(ext))
    #try:
    #    shutil.copy(report_src_file, report_dst_file)
    #except NameError:
    #    error_msg = ("No report.tsv or report.xml file found for project {}; "
    #                 "Piper processing will fail!".format(project))
    #    LOG.error(error_msg)
    #    ## TODO Pick better exception
    #    raise ValueError(error_msg)
    project.dirname = uppsala_dirname
    project.name = uppsala_dirname
    for sample in project.samples.values():
        # Naming expected by Piper; might consider whether to set sample.name as well
        if not sample.dirname.startswith("Sample_"):
            sample.dirname = "Sample_{}".format(sample.dirname)


def launch_piper_jobs(project):
    cwd = os.path.join(project.base_path, project.dirname)
    for command_line in project.command_lines:
        ## TODO Would like to log these to the log -- can we get a Logbook filehandle-like object?
        ## TODO add exception handling
        pid = execute_command_line(command_line, cwd=cwd)


def build_piper_cl(project, config):
    """Determine which workflow to run for a project and build the appropriate command line.
    :param NGIProject project: The project object to analyze.
    :param dict config: The (parsed) configuration file for this machine/environment.

    :returns: A list of Project objects with command lines to execute attached.
    :rtype: list
    :raises ValueError: If a required configuration value is missing.
    """
    # Find Piper global configuration:
    #   Check environmental variable PIPER_GLOB_CONF
    #   then the config file
    #   then the file globalConfig.xml in the piper root dir
    piper_global_config_path = (os.environ.get("PIPER_GLOB_CONF") or
                                config.get("piper", {}).get("path_to_piper_globalconfig") or
                                os.path.join(config.get("piper", {}).get("path_to_piper_rootdir"),
                                             "globalConfig.xml"))
    if not piper_global_config_path:
        error_msg = "Could not find Piper global configuration file."
        LOG.error(error_msg)
        raise ValueError(error_msg)
    try:
        ## TODO add a search for some environmental varialbe also ("PIPER_QSCRIPTS" or so)
        piper_qscripts_path = config['piper']['path_to_piper_qscripts']
    except KeyError as e:
        error_msg = "Could not load key \"{}\" from config file; " \
                    "cannot continue.".format(e)
        LOG.error(error_msg)
        raise ValueError(error_msg)
    LOG.info("Building workflow command lines for project {}".format(project))
    ## For NGI, all projects will go through the same workflows;
    ## later, we'll want to let some database values determine this.

    ## Once the coverage is high enough (check database), we'll also
    ## need to put them through e.g. the GATK

    ## We'll want to make this a generic value in the database ("QC", "DNAAlign", "VariantCalling", etc.)
    ##  and then map to the correct script in the config file. This way we can execute the same pipelines
    ##  for any of the engines

    ## This key will probably exist on the project level, and may have multiple values.
    ## Workflows may imply a number of substeps (e.g. qc, alignment, etc.)
    # workflows_for_project = proj_db.get("workflows") or something like that
    generic_workflow_names_for_project = ["dna_alignonly"]
    try:
        setup_xml_path = project.setup_xml_path
    except AttributeError:
        error_msg = ("Project {} has no setup.xml file. Skipping project "
                     "command-line generation.".format(project))
        LOG.error(error_msg)
        raise ValueError(error_msg)
    for workflow_name in generic_workflow_names_for_project:
        cl = workflows.return_cl_for_workflow(workflow_name=workflow_name,
                                              qscripts_dir_path=piper_qscripts_path,
                                              setup_xml_path=setup_xml_path,
                                              global_config_path=piper_global_config_path)
        project.command_lines.append(cl)


def build_setup_xml(project, config):
    """Build the setup.xml file for each project using the CLI-interface of
    Piper's SetupFileCreator.

    :param NGIProject project: The project to be converted.
    :param dict config: The (parsed) configuration file for this machine/environment.

    :returns: A list of Project objects with setup.xml paths as attributes.
    :rtype: list
    """
    LOG.info("Building Piper setup.xml file for project {}".format(project))
    project_top_level_dir = os.path.join(project.base_path, project.dirname)
    cl_args = {'project': project.name}

    # Load needed data from database
    try:
        # Information we need from the database:
        # - species / reference genome that should be used (hg19, mm9)
        # - analysis workflows to run (QC, DNA alignment, RNA alignment, variant calling, etc.)
        # - adapters to be trimmed (?)
        ## <open connection to project database>
        #reference_genome = proj_db.get('species')
        reference_genome = 'GRCh37'
        # sequencing_center = proj_db.get('Sequencing Center')
        cl_args["sequencing_center"] = "NGI"
    except:
        ## Handle database connection failures here once we actually try to connect to it
        pass

    # Load needed data from configuration file
    try:
        cl_args["reference_path"] = config['supported_genomes'][reference_genome]
        cl_args["uppmax_proj"] = config['environment']['project_id']
    except KeyError as e:
        error_msg = ("Could not load required information from"
                     " configuration file and cannot continue with project {}:"
                     " value \"{}\" missing".format(project, e.message))
        LOG.error(error_msg)
        raise ValueError(error_msg)

    try:
        cl_args["sfc_binary"] = config['piper']['path_to_setupfilecreator']
    except KeyError:
        # Assume setupFileCreator is on path
        cl_args["sfc_binary"] = "setupFileCreator"

    output_xml_filepath = os.path.join( project_top_level_dir,
                                        "{}_setup.xml".format(project))
    cl_args["output_xml_filepath"] = output_xml_filepath
    cl_args["sequencing_tech"] = "Illumina"

    setupfilecreator_cl = ("{sfc_binary} "
                           "--output {output_xml_filepath} "
                           "--project_name {project} "
                           "--sequencing_platform {sequencing_tech} "
                           "--sequencing_center {sequencing_center} "
                           "--uppnex_project_id {uppmax_proj} "
                           "--reference {reference_path}".format(**cl_args))
    for sample in project.samples.values():
        for fcid in sample:
            sample_directory = os.path.join(project_top_level_dir, fcid.dirname, sample.dirname)
            setupfilecreator_cl += " --input_sample {}".format(sample_directory)

    try:
        LOG.info("Executing command line: {}".format(setupfilecreator_cl))
        subprocess.check_call(shlex.split(setupfilecreator_cl))
        project.setup_xml_path = output_xml_filepath
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        error_msg = ("Unable to produce setup XML file for project {}; "
                     "skipping project analysis. "
                     "Error is: \"{}\". .".format(project, e))
        LOG.error(error_msg)
        raise RuntimeError(error_msg)


def create_report_tsv(project):
    """Generate a tsv-formatted file as input for Piper and write to top level of project,
    unless a report.xml file exists already (as it will for Uppsala projects).
    Produces one report.tsv for each project, if the report.xml does not exist.

    This file has the format:

        #SampleName     Lane    ReadLibrary     FlowcellID
        P567_102        1       A               AH0JYUADXX
        P567_102        2       B               AH0JYUADXY

    :param NGIProject project: The project to be converted.
    """
    report_header = ("#SampleName", "Lane", "ReadLibrary", "FlowcellID")

    report_paths = []
    report_tsv_path = os.path.join(project.base_path, project.name, "report.tsv")
    report_xml_path = os.path.join(project.base_path, project.name, "report.xml")
    ## TODO I think we might need to replace this file if the project changes
    ##      -- might be cheapest to just generate a new one every time
    if os.path.exists(report_xml_path):
        report_paths.append(report_xml_path)
        LOG.info("Found preexisting report.xml file for project {project}: " \
                 "{report_xml}".format(project, report_xml_path))

    #if os.path.exists(report_tsv_path):
    #    path, orig_filename = os.path.split(report_tsv_path)
    #    orig_basename, orig_ext = os.path.splitext(orig_filename)
    #    mv_filename = orig_basename + time.strftime("_%Y-%m-%d_%H:%M:%S") + orig_ext
    #    mv_path = os.path.join(path, mv_filename)
    #    LOG.info("Moving preexisting report.tsv file to {}".format(mv_path))
    #    shutil.move(report_tsv_path, mv_path)
    with open(report_tsv_path, 'w') as rtsv_fh:
        report_paths.append(report_tsv_path)
        LOG.info("Writing {}".format(report_tsv_path))
        print("\t".join(report_header), file=rtsv_fh)
        for sample in project:
            for fcid in sample:
                fcid_path = os.path.join(project.base_path,
                                         project.dirname,
                                         sample.dirname,
                                         fcid.dirname)
                for fq_pairname in find_fastq_read_pairs_from_dir(directory=fcid_path).keys():
                    try:
                        lane = parse_lane_from_filename(fq_pairname)
                    except ValueError as e:
                        LOG.error("Could not get lane from filename for file {} -- skipping ({})".format(fq_pairname, e))
                        raise ValueError(error_msg)
                    ## TODO pull from Charon database
                    read_library = "<NotImplemented>"
                    print("\t".join([sample.name, lane, read_library, fcid.name]), file=rtsv_fh)