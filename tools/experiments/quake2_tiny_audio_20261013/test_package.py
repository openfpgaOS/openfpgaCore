#!/usr/bin/env python3
"""Negative integrity/path tests for portable verification and staging."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
sys.dont_write_bytecode=True
import package


def fixture(root, kind='normal'):
    d=root/'app';d.mkdir()
    content=b'int main(void){return 0;}\n'
    name={'traversal':'../escape','absolute':'/escape','dot':'src/./x'}.get(kind,'src/main.c')
    with gzip.open(d/'source.tar.gz','wb')as gz,tarfile.open(fileobj=gz,mode='w')as tar:
        count=2 if kind=='duplicate' else 0 if kind=='missing' else 1
        for _ in range(count):
            m=tarfile.TarInfo(name);m.size=len(content);m.mode=0o644
            if kind in ('symlink-member','hardlink-member'):
                m.type=tarfile.SYMTYPE if kind=='symlink-member' else tarfile.LNKTYPE;m.linkname='../escape';m.size=0
            if kind=='suid':m.mode=0o4644
            tar.addfile(m,io.BytesIO(content)if m.isfile()else None)
    (d/'app.elf').write_bytes(b'fixture-only')
    pins={name:dict(sha256=hashlib.sha256(content).hexdigest(),mode=0o644)}
    if kind=='source-tamper':pins[name]['sha256']='0'*64
    manifest=dict(format=1,component='app',archive='source.tar.gz',source=pins,expected_elf_sha256=package.sha(d/'app.elf'),files=package.inventory(d))
    if kind=='archive-traversal':manifest['archive']='../../escape'
    (d/'manifest.json').write_text(json.dumps(manifest))


def source_fixture(root):
    d=root/'core-source';d.mkdir()
    contents={'src/fpga/targets/pocket/ap_core.qsf':b'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA\n',
              'src/fpga/common/fixture.v':b'module fixture; endmodule\n'}
    with tarfile.open(d/'source.tar.gz','w:gz')as tar:
        for name,data in contents.items():
            m=tarfile.TarInfo(name);m.size=len(data);m.mode=0o644;tar.addfile(m,io.BytesIO(data))
    manifest=dict(format=1,component='core-source',archive='source.tar.gz',qualified=False,
        selected_seed=None,timing_closed_at_configured_100_mhz=False,
        synthesis_settings=package.optimization_settings(contents['src/fpga/targets/pocket/ap_core.qsf'].decode()),
        source={n:dict(sha256=hashlib.sha256(d).hexdigest(),mode=0o644)for n,d in contents.items()},files=package.inventory(d))
    (d/'manifest.json').write_text(json.dumps(manifest));return manifest


def main():
    checks=[]
    def rejects(label,action):
        try:action()
        except (ValueError,FileExistsError):checks.append(label);return
        raise AssertionError('Unexpected acceptance: '+label)
    with tempfile.TemporaryDirectory(prefix='tiny-audio-package-test-')as t:
        base=Path(t)
        cpu='set_instance_assignment -name OPTIMIZATION_TECHNIQUE SPEED -to ic|cpu|cpu'
        settings=package.optimization_settings('set_global_assignment -name OPTIMIZATION_TECHNIQUE SPEED\n'+cpu+'\nset_global_assignment -name OPTIMIZATION_TECHNIQUE AREA\n')
        assert settings['global_setting']==dict(effective='AREA',assignments=['SPEED','AREA'])and settings['instance_assignments']==[cpu]
        checks.append('effective-area-with-cpu-speed-history')
        assert package.optimization_settings('')['global_setting']==dict(effective='BALANCED',assignments=[])
        checks.append('default-optimization-recorded')
        rejects('unsupported-optimization-assignment',lambda:package.optimization_settings('set_global_assignment -name OPTIMIZATION_TECHNIQUE $arbitrary'))
        physical='set_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC OFF\nset_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC ON\nset_global_assignment -name PHYSICAL_SYNTHESIS_EFFORT NORMAL\nset_global_assignment -name PHYSICAL_SYNTHESIS_EFFORT FAST\n'
        settings=package.optimization_settings(physical)['physical_synthesis']
        assert settings['PHYSICAL_SYNTHESIS_COMBO_LOGIC']==dict(effective='ON',assignments=['OFF','ON'])
        assert settings['PHYSICAL_SYNTHESIS_EFFORT']==dict(effective='FAST',assignments=['NORMAL','FAST'])
        checks.append('physical-settings-history-and-effective-values')
        rejects('unsupported-physical-setting',lambda:package.optimization_settings('set_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC $arbitrary'))
        rejects('unsupported-physical-effort',lambda:package.optimization_settings('set_global_assignment -name PHYSICAL_SYNTHESIS_EFFORT UNKNOWN'))
        protected=cpu+'\nset_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_RETIMING OFF\n'
        assert package.functional_qsf(protected)==package.functional_qsf(protected+physical)
        assert len(package.functional_qsf(protected))==2
        checks.append('instance-and-retiming-settings-remain-protected')
        scoped='set_instance_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC ON -to ic|cpu|cpu'
        expected=dict(present=True,target='ic|cpu|cpu',effective='ON',assignments=[dict(target='ic|cpu|cpu',value='ON')])
        assert package.cpu_combo_scope(scoped)==expected
        assert package.functional_qsf(protected+scoped)==package.functional_qsf(protected)
        checks.append('exact-cpu-combo-scope-recorded')
        quoted='set_instance_assignment\t-name "PHYSICAL_SYNTHESIS_COMBO_LOGIC" {ON} -to {ic|cpu|cpu}'
        assert package.cpu_combo_scope(quoted)==expected
        assert package.cpu_combo_scope(scoped+'\n'+quoted)['assignments']==expected['assignments']*2
        checks.append('cpu-combo-quoting-and-complete-history')
        rejects('cpu-combo-wildcard',lambda:package.cpu_combo_scope(scoped+'|*'))
        rejects('cpu-combo-other-target',lambda:package.cpu_combo_scope(scoped.replace('ic|cpu|cpu','ic|gpu')))
        rejects('cpu-combo-off',lambda:package.cpu_combo_scope(scoped.replace(' ON ',' OFF ')))
        rejects('cpu-combo-command-injection',lambda:package.cpu_combo_scope(scoped+'; set x 1'))
        source_root=base/'source-only';source_root.mkdir();unqualified=source_fixture(source_root)
        package.verify_component('core-source',source_root)
        package.stage('core-source',base/'source-stage',source_root)
        assert not (source_root/'core').exists() and not list(source_root.rglob('*.rbf_r'))
        checks.append('unqualified-source-only-verify-stage-without-release-core')
        rejects('source-only-nonnull-selection',lambda:package.check_unqualified(dict(unqualified,selected_seed=2),unqualified['files']))
        rejects('source-only-qualified-claim',lambda:package.check_unqualified(dict(unqualified,qualified=True),unqualified['files']))
        rejects('source-only-clock-closure-claim',lambda:package.check_unqualified(dict(unqualified,timing_closed_at_configured_100_mhz=True),unqualified['files']))
        rejects('source-only-selected-artifact-claim',lambda:package.check_unqualified(dict(unqualified,expected_artifact_sha256='0'*64),unqualified['files']))
        missing=dict(unqualified);del missing['selected_seed']
        rejects('source-only-missing-null-selection',lambda:package.check_unqualified(missing,unqualified['files']))
        for suffix in ('.rbf','.rbf_r','.sof'):
            rejects('source-only-binary'+suffix,lambda suffix=suffix:package.check_unqualified(unqualified,[*unqualified['files'],'core'+suffix]))
        (source_root/'core-source/os30.rbf_r').write_bytes(b'not-a-release')
        unqualified['files']=package.inventory(source_root/'core-source');unqualified['files'].pop('manifest.json')
        (source_root/'core-source/manifest.json').write_text(json.dumps(unqualified))
        rejects('source-only-inventory-cannot-authorize-bitstream',lambda:package.verify_component('core-source',source_root))
        for kind in ['normal','traversal','absolute','dot','duplicate','missing','symlink-member','hardlink-member','suid','source-tamper','archive-traversal']:
            root=base/kind;root.mkdir();fixture(root,kind)
            if kind=='normal':
                package.verify_component('app',root);package.stage('app',base/'stage-good',root);checks.append('good-verify-stage')
                rejects('existing-destination',lambda:package.stage('app',base/'stage-good',root))
                (base/'linked-parent').symlink_to(base/'stage-good',target_is_directory=True)
                rejects('linked-destination-parent',lambda:package.stage('app',base/'linked-parent/new',root))
            else:rejects(kind,lambda root=root:package.verify_component('app',root))
        for kind in ['tampered-payload','unlisted-file','linked-payload','linked-source-directory']:
            root=base/kind;root.mkdir();fixture(root)
            if kind=='tampered-payload':(root/'app/app.elf').write_bytes(b'changed')
            elif kind=='unlisted-file':(root/'app/extra').write_bytes(b'extra')
            elif kind=='linked-payload':
                (root/'app/app.elf').unlink();(root/'app/app.elf').symlink_to(base/'normal/app/app.elf')
            else:
                (root/'app').rename(root/'real-app');(root/'app').symlink_to(root/'real-app',target_is_directory=True)
            rejects(kind,lambda root=root:package.verify_component('app',root))
        (base/'stage-good/src/main.c').write_bytes(b'changed')
        rejects('staged-source-tamper',lambda:package.check_stage(base/'stage-good','app',base/'normal'))
    print(json.dumps(dict(passed=True,checks=checks),indent=2))


if __name__=='__main__':main()
