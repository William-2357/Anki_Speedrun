// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

use anyhow::Result;

use crate::action::BuildAction;
use crate::build::BuildProfile;
use crate::build::FilesHandle;
use crate::cargo::CargoBuild;
use crate::cargo::RustOutput;
use crate::glob;
use crate::inputs;
use crate::Build;

pub struct ConfigureBuild {}

impl BuildAction for ConfigureBuild {
    fn command(&self) -> &str {
        "$cmd"
    }

    fn files(&mut self, build: &mut impl FilesHandle) {
        build.add_inputs("cmd", inputs![":build:configure_bin"]);
        // reconfigure when external inputs change
        build.add_inputs("", inputs!["$builddir/env", ".version"]);
        // In the Anki Speedrun monorepo the git dir lives at the repo root
        // rather than in this folder; depend on whichever exists so ninja
        // doesn't error out on a missing `.git` input.
        for git_dir in [".git", "../.git"] {
            if std::path::Path::new(git_dir).exists() {
                build.add_inputs("", inputs![git_dir]);
                break;
            }
        }
        build.add_outputs("", ["build.ninja"])
    }

    fn on_first_instance(&self, build: &mut Build) -> Result<()> {
        build.add_action(
            "build:configure_bin",
            CargoBuild {
                inputs: inputs![glob!["build/**/*"]],
                outputs: &[RustOutput::Binary("configure")],
                target: None,
                extra_args: "-p configure",
                release_override: Some(BuildProfile::Debug),
            },
        )?;
        Ok(())
    }

    fn generator(&self) -> bool {
        true
    }

    fn check_output_timestamps(&self) -> bool {
        true
    }
}
