import sys
import os
import argparse
import re
import shlex
import subprocess
from statistics import mean

class BrokenRegexException(Exception):
	"""Raised when the regex strings in test_package() may be broken."""
	pass

class PackageNotFoundException(Exception):
	"""Raised when a package is not found by get_path"""
	pass

class TestFailedException(Exception):
	"""Raised in the event a package fails 1 or more tests"""
	def __init__(self):
		self.numFailedPackages = 0

class Package():
	"""Class for storing information about a package"""
	def __init__(self, name, path=None):
		self.name = name
		self.path = path or get_path(name)
		self.lineCount = count_lines_of_code(path)
		self.hasTestDir = hasTestDir(path)
		self.coverage_total = None
		self.coverage_lines = None
		self.coverage_functions = None
		self.weight = None

def test_package(pkg, extra_args=["--force-color"], 
					cmake_build_type="Debug",
					suppress_catkin_output=False,
					verbose=True):
	"""
	Runs tests for pkg at path using test_cmd and returns the total coverage
	for the given package at the given path if one is given. If any tests fail, 
	TestFailedException will be thrown.

	Required arguments:
		pkg				(string) The package to test and obtain coverage for

	Optional arguments:
		path 			(string) Used for providing a path to test the package
							at. If one is not given, the path will be obtained
							using `catkin locate` from the current directory.
		test_args		(string) Use this as an override for the command args.
		cmake_build_type
						(string) Provide a build type to use with catkin build.
							Default: `Debug`
		suppress_catkin_output
						(bool) Enable or disable whether to display catkin output
		verbose			(bool) Enable or disable extra debug information
	"""

	if not pkg:
		raise Exception("<pkg> argument is required for test_package()")

	if verbose:
		print("Testing package %s" % pkg.name)

	test_cmd = "catkin build "+pkg.name+" -v --no-status --no-deps "+" ".join(extra_args)+" --catkin-make-args "+pkg.name+"_coverage_report"

	re_failed_test = re.compile(r"(?<=Failed: )[0-9]*(?= packages failed.)") # matches for # packages failed
	re_coverage_pct = re.compile(r"Overall coverage rate:")
	re_line_pct = re.compile(r"\d*\.\d*(?=%)") # matches to decimal value of percentage

	# Test the package
	print("Testing package %s..." % pkg)
	subprocess.run(shlex.split("catkin config -DENABLE_COVERAGE_TESTING=ON -DCMAKE_BUILD_TYPE=%s" % cmake_build_type), stdout=subprocess.DEVNULL)
	proc = subprocess.Popen(shlex.split(test_cmd), universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
	pcts = []
	get_pkg_percentages = False 
	line_coverage = 0
	function_coverage = 0
	
	# run tests on the package only if it contains a test/ directory
	if not hasTestDir(pkg.path):
		print("Package %s has no tests, so its coverage is 0%%" % pkg)
		return 0

	for line in proc.stdout:

		if not suppress_catkin_output:
			sys.stdout.write(line)

		if get_pkg_percentages:
			if not pcts: # grab line_coverage
				matches = re.findall(re_line_pct, line)
				if not matches:
					raise BrokenRegexException("Error: possible broken regex in coverage.py (re_line_pct)")
				val = float(matches[0])
				line_coverage = val
				pcts.append(val)
			else: # grab function_coverage
				matches = re.findall(re_line_pct, line)
				if not matches:
					raise BrokenRegexException("Error: possible broken regex in coverage.py (re_line_pct)")
				val = float(matches[0])
				function_coverage = val
				pcts.append(val)
				get_pkg_percentages = False

		if re.match(re_coverage_pct, line):
			get_pkg_percentages = True
		
		num_failed_tests = 0
		match = re.match(re_failed_test, line)
		if match:
			num_failed_tests = match
		if num_failed_tests > 0:
			sys.stderr.write("Unit tests failed for package: "+pkg.name+"\n")
			raise TestFailedException()

	proc.wait()

	if not pcts: # no percentages grabbed
		raise BrokenRegexException("Error: possible broken regex in coverage.py (re_coverage_pct)")

	total = mean([pcts[0], pcts[1]])
	pkg.coverage_lines = line_coverage
	pkg.coverage_functions = function_coverage
	pkg.coverage_total = total

	return total

def hasTestDir(path):
	for fname in os.listdir(path):
		if os.path.isdir(os.path.join(path, fname)) and fname.lower() == "test":
			return True
	return False

def count_lines_of_code(path):
	try:
		proc1 = subprocess.Popen(shlex.split("cloc %s --quiet --csv" % path), stdout=subprocess.PIPE)
		proc2 = subprocess.Popen(shlex.split("grep -i ',C++'"), stdin=proc1.stdout, stdout=subprocess.PIPE)
		proc1.stdout.close()
		linecount = proc2.stdout.read().decode(sys.stdout.encoding)
		return int(linecount[linecount.rfind(',')+1:])
	except FileNotFoundError:
		sys.exit("Error: cloc not installed (run `sudo apt install cloc`)")
	except ValueError:
		print("Could not get C++ line count at %s, so its linecount is 0" % path)
		return 0

def get_path(pkg, verbose=True):
	"""Queries 'catkin locate' and returns the absolute path of 'pkg' in the 
	current workspace.
	"""
	path = ""
	try:
		proc = subprocess.Popen(["catkin", "locate", pkg], universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
		path = proc.stdout.read()
		if path.startswith('ERROR'):
			raise PackageNotFoundException("Package %s not found with catkin locate:\n\t%s" % (pkg, path))
		if verbose:
			print("Found package %s at path %s" % (pkg, path.rstrip()))
		return path.rstrip()
	except FileNotFoundError:
		sys.exit("Error: Roscov: searching for package '%s' failed. Catkin is likely not installed." % pkg)

def get_package(pkg, verbose=True):
	path = get_path(pkg, verbose=verbose)
	return Package(pkg, path)

def print_results(packages, unfound=None, failed=None, threshold=None, verbose=False, summaries=True):
	"""Used for printing the final output of the tool

	required arguments:
		packages 			list of Package objects tested

	optional arguments:
		unfound_packages 	list of unfound packages in the test
		failed_packages		list of failed packages in the test
	"""
	for pkg in unfound:
		sys.stderr.write("Package not found: "+pkg+"\n")
		packages.remove(pkg)

	if not packages:
		sys.exit("Error: no coverage data generated for given list of packages")

	total_weighted_coverage = 0
	totallines = 0
	valid_packages = []
	for pkg in packages: # Print summaries
		if not pkg.coverage_total:
			print("Coverage summary unavailable for '%s' (%s)" % (pkg.name, pkg.path))
		else:
			print("Coverage summary for '%s' (%s):\n\t%s%% line coverage\n\t%s%% function coverage\n\t%s%% total coverage" % (pkg.name, pkg.path, pkg.coverage_lines, pkg.coverage_functions, pkg.coverage_total))
			totallines += pkg.lineCount
			valid_packages.append(pkg)

	for pkg in valid_packages: # Print calculations
		if verbose:
			print("Package '%s' has average coverage of %s%%, and contains %s lines out of %s total lines being tested." % (pkg.name, pkg.coverage_total, pkg.lineCount, totallines))
		total_weighted_coverage += float(pkg.lineCount) / float(totallines) * float(pkg.coverage_total)
	
	print("Total coverage: %s%%" % str(round(total_weighted_coverage, 2)))
	results = subprocess.Popen("catkin_test_results", stdout=subprocess.PIPE)
	if verbose:
		for line in results.stdout:
			print(line)
	results.wait()

	if threshold and total_weighted_coverage < threshold:
		sys.exit("Resulting total coverage is below threshold of %s%%. Script exited with exit code 1." % str(round(threshold, 2)))

if __name__ == "__main__":
	parser = argparse.ArgumentParser(usage='code_coverage.py [<args>] [<packages>]')
	parser.add_argument('--quiet', dest='quiet', action='store_true', default=False, 
							help='Suppress normal catkin output and only print package summaries')
	parser.add_argument('--verbose', dest='verbose', action='store_true', default=None, 
							help='Show debug statements from function calls')
	parser.add_argument('--no-summary', dest='summary', action='store_true', default=False, 
							help="Show summary of each package's test results.")
	parser.add_argument('--threshold', dest='threshold', type=int, default=None, 
							help='Any resulting total coverage below this number results in an exit code of 1')
	
	parser.add_argument('packages', metavar='packages', nargs='*')
	args = parser.parse_args(sys.argv[1:])

	packages = args.packages
	if len(packages) == 1:
		packages = packages[0].split(" ")

	if args.quiet:
		args.verbose = False

	unfound_packages = []
	failed_packages = []
	test_packages = []

	for pkg in packages:
		try:
			test_packages.append(get_package(pkg, verbose=args.verbose))
		except PackageNotFoundException:
			unfound_packages.append(pkg)

	for pkg in test_packages:
		try:
			if pkg.hasTestDir:
				coverage = test_package(pkg, suppress_catkin_output=args.quiet, verbose=args.verbose)
				if not coverage:
					sys.exit("Error: no coverage data obtained for package '%s'" % pkg.name) 
			else:
				print("Passing over %s, package has no subdirectory named `test` (%s)" % (pkg.name, pkg.path))

		except TestFailedException:
			failed_packages.append(pkg.name)

	print_results(test_packages, unfound=unfound_packages, failed=failed_packages, threshold=args.threshold)
