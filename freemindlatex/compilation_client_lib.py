"""Client-side of the latex compilation service.
"""

import os
import time

import grpc

from absl import flags, logging
from freemindlatex import compilation_service_pb2, compilation_service_pb2_grpc

flags.DEFINE_string("watched_file_extensions", "mm,png,jpg,pdf",
                    "Files extensions to watch for LaTeX compilation.")
flags.DEFINE_integer(
  "max_health_retries",
  5,
  "Number of health check retries before giving up.")
flags.DEFINE_string("latex_error_log_filename", "latex.log",
                    "Log file for latex compilation errors.")


def _GetMTime(filename):
  """Get the time of the last modification.
  """
  try:
    return os.path.getmtime(filename)
  except OSError as _:          # file does not exist.
    return None


def GetMTimeListForDir(directory, excludes=[]):
  """Getting the modification time for all user files in a directory.

  Returns: a sorted list of pairs in form of ('file1', 1234567), where the paths
    are relative paths
  """
  suffixes = [
    '.%s' %
    i for i in flags.FLAGS.watched_file_extensions.split(',')]
  mtime_list = []
  excludes = set(os.path.relpath(
    name, directory) for name in excludes)
  for dirpath, _, filenames in os.walk(directory):
    for filename in [f for f in filenames if any(
        f.endswith(suf) for suf in suffixes)]:
      filepath = os.path.join(dirpath, filename)
      if filepath in excludes:
        continue
      mtime_list.append(
        (os.path.relpath(
          filepath,
          directory),
         _GetMTime(filepath)))
  return sorted(mtime_list)


class LatexCompilationClient(object):
  """Client-side of latex compilation.
  """

  def __init__(self, server_address):
    self._channel = grpc.insecure_channel(server_address)
    self._healthz_stub = compilation_service_pb2_grpc.HealthStub(self._channel)
    self._compilation_stub = compilation_service_pb2_grpc.LatexCompilationStub(
      self._channel)

  def CheckHealthy(self):
    try:
      response = self._healthz_stub.Check(
        compilation_service_pb2.HealthCheckRequest())
    except grpc.RpcError as e:
      if e.code() == grpc.StatusCode.UNAVAILABLE:
        return False
      raise
    return (response.status ==
            compilation_service_pb2.HealthCheckResponse.SERVING)

  @staticmethod
  def GetCompiledDocPath(directory):
    """Get path to the compiled PDF file.

    Args:
      directory: The directory where the compilations happend.
        e.g. /tmp/testdir

    Returns:
      The file name of the output document, e.g. /tmp/testdir/testdir.pdf
    """
    return os.path.join(
      directory, "{}.pdf".format(os.path.basename(directory)))

  def CompileDir(self, directory, mode):
    """Compiles the files in user's directory, and update the pdf file.

    The function will prepare the directory content, send it over for
    compilation. When there is a latex compilation error, we will put
    the latex error log at latex.log (or anything else specified by
    latex_error_log_filename).

    Args:
      directory: directory where user's files locate
      mode: the way to compile,
        e.g. compilation_service_pb2.LatexCompilationRequest.BEAMER

    Returns: boolean indicating if the compilation was successful.
      When unceccessful, leaves log files.
    """
    target_pdf_loc = self.GetCompiledDocPath(directory)

    filename_and_mtime_list = GetMTimeListForDir(
      directory, excludes=[target_pdf_loc])
    compilation_request = compilation_service_pb2.LatexCompilationRequest()
    for filename, _ in filename_and_mtime_list:
      with open(os.path.join(directory, filename)) as infile:
        new_file_info = compilation_request.file_infos.add()
        new_file_info.filepath = filename
        new_file_info.content = infile.read()
    compilation_request.compilation_mode = mode

    response = self._compilation_stub.CompilePackage(compilation_request)
    if response.pdf_content:
      open(target_pdf_loc, 'w').write(response.pdf_content)

    if (response.status !=
        compilation_service_pb2.LatexCompilationResponse.SUCCESS):
      latex_log_file = os.path.join(
        directory, flags.FLAGS.latex_error_log_filename)
      with open(latex_log_file, 'w') as ofile:
        ofile.write(response.compilation_log)

    return (response.status ==
            compilation_service_pb2.LatexCompilationResponse.SUCCESS)


def WaitTillHealthy(server_address):
  """Wait until the server is healthy, with RPC calls.
  """
  client = LatexCompilationClient(server_address)
  retries = 0
  while not client.CheckHealthy() and retries < flags.FLAGS.max_health_retries:
    retries += 1
    logging.info("Compilation server not healthy yet (%d's retry)", retries)
    time.sleep(1)
