import tempfile
from os import listdir
from pathlib import Path
from unittest import TestCase

from tqdm import tqdm

from veniq.dataset_collection.augmentation import analyze_file


class IntegrationDatasetCollection(TestCase):

    def test_dataset_collection(self):
        samples_path = Path(__file__).absolute().parent / "dataset_collection"
        results_predefined = [
            ['GlobalShortcutConfigForm.java',
             'GlobalShortcutConfigForm', 'loadConfig();', 180, 186, 'initComponents'],
            ['GlobalShortcutConfigForm.java',
             'GlobalShortcutConfigForm', 'refresh();', 238, 318, 'loadConfig'],
            ['GlobalShortcutConfigForm.java',
             'GlobalShortcutConfigForm', 'this.initComponents();', 82, 88, 'GlobalShortcutConfigForm'],
            ['HudFragment.java', 'HudFragment', 'toggleMenus();',
             131, 510, 'build'],
            ['HudFragment.java', 'HudFragment',
             'showLaunchConfirm();', 590, 479, 'addWaveTable'],
            ['PlanetDialog.java', 'PlanetDialog', 'makeBloom();', 70,
             147, 'PlanetDialog'],
            ['PlanetDialog.java', 'PlanetDialog',
             'updateSelected();', 136, 334, 'PlanetDialog'],
            ['ReaderHandler.java', 'ReaderHandler',
             'receiveMessage();', 183, 115, 'onWebSocketConnect'],
            ['ReaderHandler.java', 'ReaderHandler',
             'receiveMessage();', 196, 115, 'onWebSocketText'],
            ['ReaderHandler.java', 'ReaderHandler',
             'final int receiverQueueSize = getReceiverQueueSize();', 76, 253, 'ReaderHandler'],
            ['ToggleProfilingPointAction.java',
             'ToggleProfilingPointAction', 'ProfilingPointsSwitcher chooserFrame = getChooserFrame();', 272, 311,
             'actionPerformed'],
            ['ToggleProfilingPointAction.java',
             'ToggleProfilingPointAction', 'nextFactory();', 284, 361, 'actionPerformed'],
            ['ToggleProfilingPointAction.java',
             'ToggleProfilingPointAction', 'resetFactories();', 289, 369, 'actionPerformed'],
            ['ToggleProfilingPointAction.java',
             'ToggleProfilingPointAction',
             'if (acceleratorModifiers != eventKeyStroke.getModifiers()) modifierKeyStateChanged();', 308, 324,
             'eventDispatched'],
            ['ToggleProfilingPointAction.java',
             'ToggleProfilingPointAction', 'ProfilingPointsSwitcher chooserFrame = getChooserFrame();', 329, 311,
             'modifierKeyStateChanged']]

        results_output = []
        with tempfile.TemporaryDirectory() as tmpdirname:
            print('created temporary directory', tmpdirname)
            for filepath in tqdm(listdir(samples_path)):
                full_filename = samples_path / filepath
                try:
                    results_output.extend(analyze_file(full_filename, tmpdirname))
                except Exception as e:
                    raise RuntimeError(
                        f"Failed to run analyze function in file {full_filename}"
                    ) from e

            new_results = []
            for x in results_output:
                x[0] = Path(x[0]).name
                new_results.append(x)
        self.assertEqual(new_results.sort(), results_predefined.sort())
