"""Tests around archives"""

import pytest
import tarfile
from pathlib import Path

from flint.archive import (
    ArchiveOptions,
    create_sbid_tar_archive,
    resolve_glob_expressions,
    get_parser,
    tar_files_into,
    DEFAULT_GLOB_EXPRESSIONS,
)

FILES = [f"some_file_{a:02d}-image.fits" for a in range(36)] + [
    f"a_validation.{ext}" for ext in ("png", "jpeg", "pdf")
]


@pytest.fixture
def glob_files(tmpdir):
    """Create an example set of temporary files in a known directory"""
    for f in FILES:
        touch_file = f"{str(tmpdir / f)}"
        with open(touch_file, "w") as out_file:
            out_file.write("example\n")

    return (tmpdir, FILES)


@pytest.fixture
def temp_files(glob_files):
    base_dir, files = glob_files

    archive_options = ArchiveOptions()

    resolved = resolve_glob_expressions(
        base_path=base_dir, file_globs=archive_options.file_globs
    )

    return (base_dir, resolved)


def test_glob_expressions(glob_files):
    """Trying out the globbing from the archive options"""
    base_dir, files = glob_files

    archive_options = ArchiveOptions()
    assert len(archive_options.file_globs) > 0

    resolved = resolve_glob_expressions(
        base_path=base_dir, file_globs=archive_options.file_globs
    )

    assert all([isinstance(p, Path) for p in resolved])
    assert len(resolved) == 37


def test_glob_expressions_uniq(glob_files):
    """Make sure that the uniqueness is correct"""
    base_dir, files = glob_files

    archive_options = ArchiveOptions(file_globs=("*png", "*png"))
    resolved = resolve_glob_expressions(
        base_path=base_dir, file_globs=archive_options.file_globs
    )
    assert len(resolved) == 1


def test_glob_expressions_empty(glob_files):
    """Make sure that the uniqueness is correct"""
    base_dir, files = glob_files

    archive_options = ArchiveOptions(file_globs=("*doesnotexist",))
    resolved = resolve_glob_expressions(
        base_path=base_dir, file_globs=archive_options.file_globs
    )
    assert len(resolved) == 0


def test_archive_parser(glob_files):
    """Make sure pirates understand parsers"""
    base_dir, files = glob_files
    parser = get_parser()

    args = parser.parse_args("list".split())

    assert isinstance(args.base_path, Path)
    assert args.file_globs == DEFAULT_GLOB_EXPRESSIONS

    example_path = Path("this/no/exist")
    args = parser.parse_args(f"list --base-path {str(example_path)}".split())
    assert isinstance(args.base_path, Path)
    assert args.base_path == example_path

    example_path = Path(base_dir)
    args = parser.parse_args(
        f"list --base-path {str(example_path)} --file-globs *pdf".split()
    )
    assert isinstance(args.base_path, Path)
    assert args.base_path == example_path
    assert args.file_globs == ["*pdf"]


def test_tar_ball_files(temp_files):
    """Ensure that the tarballing works"""
    base_dir, files = temp_files

    tar_out_path = Path(base_dir) / "some_tarball.tar"
    _ = tar_files_into(tar_out_path=tar_out_path, files_to_tar=files)

    assert tarfile.is_tarfile(tar_out_path)

    with pytest.raises(FileExistsError):
        _ = tar_files_into(tar_out_path=tar_out_path, files_to_tar=files)


def test_create_sbid_archive(glob_files):
    """Attempts to ensure the entire find files and tarball creation works"""
    base_dir, files = glob_files

    archive_options = ArchiveOptions()
    tar_out_path = Path(base_dir) / "example_tarball_2.tar"

    _ = create_sbid_tar_archive(
        base_path=base_dir, tar_out_path=tar_out_path, archive_options=archive_options
    )

    assert tarfile.is_tarfile(tar_out_path)
