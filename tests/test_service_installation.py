import pytest
from scripts.install_service import render_unit, install


def test_service_uses_project_directory_and_distinct_launchers(tmp_path):
    for role in ('master', 'worker'):
        unit=render_unit(role,tmp_path,'tasker')
        assert f'WorkingDirectory={tmp_path}' in unit
        assert f'ExecStart={tmp_path}/scripts/start_{role}.sh' in unit
        assert 'User=tasker' in unit and 'Restart=on-failure' in unit
        assert 'EnvironmentFile=' not in unit  # Launchers select their own env file.


@pytest.mark.parametrize('role,user,path', [('bad','tasker','/data/tasker'),('master','root','/data/tasker'),('master','tasker\nUser=root','/data/tasker'),('master','tasker','/data/tasker%u')])
def test_invalid_service_definitions_are_rejected(role,user,path):
    with pytest.raises(ValueError):render_unit(role,path,user)


def test_service_installation_requires_linux_systemd(monkeypatch,tmp_path):
    monkeypatch.setattr('scripts.install_service.sys.platform','darwin')
    with pytest.raises(ValueError,match='Linux/systemd'):
        install('master',tmp_path,'tasker')


@pytest.mark.parametrize('start', [False, True])
def test_installer_writes_unit_and_controls_systemd_without_touching_real_services(monkeypatch,tmp_path,start):
    import pwd
    from scripts import install_service as module
    project=tmp_path/'project';units=tmp_path/'units';units.mkdir()
    for relative in ('.venv/bin/python','scripts/start_master.sh','.env'):
        p=project/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('test')
    monkeypatch.setattr(module,'UNIT_DIRECTORY',units)
    monkeypatch.setattr(module.sys,'platform','linux')
    monkeypatch.setattr(module.shutil,'which',lambda name:'/usr/bin/systemctl')
    monkeypatch.setattr(module.os,'geteuid',lambda:0)
    monkeypatch.setattr(pwd,'getpwnam',lambda user:object())
    commands=[]
    monkeypatch.setattr(module.subprocess,'run',lambda command,**kwargs:commands.append(command))
    install('master',project,'tasker',start)
    assert (units/'tasker-master.service').read_text()==render_unit('master',project,'tasker')
    assert commands==[['systemctl','daemon-reload']]+([['systemctl','enable','--now','tasker-master.service']] if start else [])
    assert list(units.glob('.tasker-*'))==[]
