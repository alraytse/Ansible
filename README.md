## Using dcnm_core module alongside Ansible API

You can clone this repo and create a playbook. Or you can just grab the library directory and add it to your Ansible project.

### Servers

```
login to server
```

Please activate production python virtual environment prior to executing the scripts

`source /opt/venvs/ansible_prod/bin/activate`  if not running python3

you shell prompt should update with the name of the virtual env:



### Prereqs:

requries dcnm_core module (NOA version), **already installed on servers**<br>

<br>
<br>

Right now if you use the NOA-1/NOA-2 Production Server the use the ansible_prod virtualenv which has all dependcies installed.<br>

Step 1.

```bash
# activate virtual environment

source /opt/venvs/ansible_prod/bin/activate
```

Step 2.

```bash
push latest
```

Step 3.

```
Run the playbooks
```

<br>
<br>

How to setup your "ANSIBLE PLAY"<br>

```yaml
---
- name: 'DCNM Ansible Modules'
  hosts: localhost
  gather_facts: false

  tasks:
    - name: Task 1
```

### Use cases:

- Create Network(s)

```yaml
- name: 'Cisco DCNM create network'
  dcnm_create_network:
    base_url: https://dcnm-lab.dev.schwab.com
    username: '{{ ansible_user }}'
    password: '{{ ansible_password }}'
    fabric_name: Fabric
    networks:
      - subnet: 192.168.66.0/24
        vlan: 966
      - subnet: 192.168.67.0/24
        vlan: 967
```

- Attach Network(s)
  NOTE: backout key <br><br>(True if you want to deattach networks, False if you want to attach networks.)

```yaml
- name: "Cisco DCNM overlay attach"
  dcnm_attach_overlay:
    base_url: https://dcnm-lab.dev.com
    username: "{{ ansible_user }}"
    password: "{{ ansible_password }}"
    fabric_name: Fabric
    backout: True
    networks:
      - subnet: 192.168.90.0/24
        vlan: 990
        switch:
          - name: rlf14lab
            interfaces:
              - Ethernet1/2
          - name: rlf15lab
            interfaces:
              - Ethernet1/2
      - subnet: 192.168.67.0/24
          vlan: 967
          switch:
          - name: rlf14lab
              interfaces:
                - Ethernet1/21
          - name: rlf15lab
              interfaces:
                - Ethernet1/21
```

- Interface Description(s)

```yaml
- name: 'Cisco DCNM Interface Description'
  dcnm_interface_description:
    base_url: https://dcnm-lab.dev.com
    username: '{{ ansible_user }}'
    password: '{{ ansible_password }}'
    fabric_name: Fabric
    interfaces:
      - switch: rlf14lab
        interface: Ethernet1/22
        desc: desc_by_ansible
      - switch: rlf14lab
        interface: Ethernet1/23
        desc: desc_by_ansible
      - switch: rlf15lab
        interface: Ethernet1/22
        desc: desc_by_ansible
      - switch: rlf15lab
        interface: Ethernet1/23
        desc: desc_by_ansible
```

- # Defaulting Interface(s)<br>NOTE: This will remove all overlays on a port and change to routed interface. It will put a description of "AVAILABLE" on the port.<br><b>Use with CAUTION!!</b> <i>Optional</i> flag force_disable: true can be added to disable connectivity checks<br>Warning you must have dcnm core >=0.1.1 installed

```yaml
- name: 'defaulting interfaces'
  dcnm_default_interface:
    base_url: https://dcnm-lab.dev.schwab.com
    username: '{{ ansible_user }}'
    password: '{{ ansible_password }}'
    fabric_name: PDC1-LAB-Fabric
    switches:
      rlf14lab:
        - Ethernet1/1
        - Ethernet1/2
      rlf15lab:
        - Ethernet1/1
        - Ethernet1/2
```

<i>Optional with force disable</i>

```yaml
- name: "defaulting interfaces"
  dcnm_default_interface:
    base_url: https://dcnm-lab.dev.schwab.com
    username: "{{ ansible_user }}"
    password: "{{ ansible_password }}"
    fabric_name: PDC1-LAB-Fabric
    switches:
        rlf14lab:
          - Ethernet1/1
          - Ethernet1/2
        rlf15lab:
      - Ethernet1/1
          - Ethernet1/2
  force_disable: true
```

- Update/Change Interface Policy

```yaml
- name: 'Cisco DCNM Interface Policy Change'
  dcnm_interface_policy:
    base_url: https://dcnm-lab.dev.schwab.com
    username: '{{ ansible_user }}'
    password: '{{ ansible_password }}'
    fabric_name: PDC1-LAB-Fabric
    policy:
      - name: int_trunk_host_11_1
        switch:
          - name: rlf14lab
            interfaces:
              - Ethernet1/20
              - Ethernet1/21
      - name: int_access_host_11_1
        switch:
          - name: rlf14lab
            interfaces:
              - Ethernet1/22
              - Ethernet1/23
          - name: rlf15lab
            interfaces:
              - Ethernet1/22
              - Ethernet1/23
      - name: int_routed_host_11_1
        switch:
          - name: rlf14lab
            interfaces:
              - Ethernet1/24
```

- Shut/No Shut Ports

```yaml
- name: 'Shut/No Shut List of Interfaces'
  dcnm_interface_state:
    base_url: https://dcnm-lab.dev.schwab.com
    username: '{{ ansible_user }}'
    password: '{{ ansible_password }}'
    fabric_name: PDC1-LAB-Fabric
    interfaces:
      - switch: rlf14lab
        state: enable
        interfaces:
          - Ethernet1/20
          - Ethernet1/21
      - switch: rlf15lab
        state: enable
        interfaces:
          - Ethernet1/20
          - Ethernet1/21
```

- Create a Server VPC <br>NOTE: Only for a vpc facing a server.

```yaml
- name: 'create server vpc'
  dcnm_server_vpc:
    base_url: https://dcnm-lab.dev.schwab.com
    username: '{{ ansible_user }}'
    password: '{{ ansible_password }}'
    fabric_name: PDC1-LAB-Fabric
    vpc_info:
      - vpc_id: 1200
        policy: trunk
        switch_one:
          name: rlf14lab
          po_description: test_desc_rlf14lab
          member_interface: Ethernet1/20
        switch_two:
          name: rlf15lab
          po_description: test_desc_rlf15lab
          member_interface: Ethernet1/20
      - vpc_id: 1201
        policy: access
        switch_one:
          name: rlf14lab
          po_description: test2_desc_rlf14lab
          member_interface: Ethernet1/21
        switch_two:
          name: rlf15lab
          po_description: test2_desc_rlf15lab
          member_interface: Ethernet1/21
```

- Update a Template <br> NOTE: Only working for ISE deployment at this time!!

```yaml
- name: 'Add/Update Template'
  dcnm_template:
    base_url: https://dcnm-lab.dev.schwab.com
    username: '{{ ansible_user }}'
    password: '{{ ansible_password }}'
    fabric_name: PDC1-LAB-Fabric
    backout: False
    ise_template:
      name:
        - rlf14lab
        - rlf15lab
      ise_server_ip: 1.1.1.1
      ise_secret: '"test_secret"'
      timeout: 1
```


    
