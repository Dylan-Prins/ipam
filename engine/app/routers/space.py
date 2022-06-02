from fastapi import APIRouter, Depends, Request, Response, HTTPException, Header, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException as StarletteHTTPException
from fastapi.encoders import jsonable_encoder

from pydantic import BaseModel, EmailStr, constr
from typing import Optional, List, Union, Any

import azure.cosmos.exceptions as exceptions

import re
import jwt
import time
import shortuuid
import jsonpatch
from netaddr import IPSet, IPNetwork

from app.dependencies import check_token_expired, get_admin
from app.models import VNet, SpaceReq, SpaceRes, BlockReq, BlockRes
from . import argquery

from app.routers.common.helper import (
    get_username_from_jwt,
    cosmos_query,
    cosmos_upsert,
    arg_query
)

router = APIRouter(
    prefix="/spaces",
    tags=["spaces"],
    dependencies=[Depends(check_token_expired)]
)

class VNet(BaseModel):
    """DOCSTRING"""

    id: str

class VNets(BaseModel):
    """DOCSTRING"""

    ids: List[str]

class VNetsUpdate(List[str]):
    """DOCSTRING"""

class Subnet(BaseModel):
    """DOCSTRING"""

    name: str
    prefix: str

class SubnetUtil(BaseModel):
    """DOCSTRING"""

    name: str
    prefix: str
    size: int
    used: int

class VNetExpand(BaseModel):
    """DOCSTRING"""

    name: str
    id: str
    prefixes: List[str]
    subnets: List[Subnet]
    resource_group: str
    subscription_id: str
    tenant_id: str

class VNetExpandUtil(BaseModel):
    """DOCSTRING"""

    name: str
    id: str
    prefixes: List[str]
    subnets: List[SubnetUtil]
    resource_group: str
    subscription_id: str
    tenant_id: str
    size: int
    used: int

class Reservation(BaseModel):
    """DOCSTRING"""

    id: str
    cidr: str
    userId: str #EmailStr
    createdOn: float
    status: str

class BlockBasic(BaseModel):
    """DOCSTRING"""

    name: str
    cidr: str
    vnets: List[str]
    resv: List[Reservation]

class BlockBasicUtil(BaseModel):
    """DOCSTRING"""

    name: str
    cidr: str
    vnets: List[str]
    resv: List[Reservation]
    size: int
    used: int

class Block(BaseModel):
    """DOCSTRING"""

    name: str
    cidr: str
    vnets: List[str]
    resv: List[Reservation]

class BlockExpand(BaseModel):
    """DOCSTRING"""

    name: str
    cidr: str
    vnets: List[VNetExpand]
    resv: List[Reservation]

class BlockUtil(BaseModel):
    """DOCSTRING"""

    name: str
    cidr: str
    vnets: List[str]
    resv: List[Reservation]
    size: int
    used: int

class BlockExpandUtil(BaseModel):
    """DOCSTRING"""

    name: str
    cidr: str
    vnets: List[VNetExpandUtil]
    resv: List[Reservation]
    size: int
    used: int

class SpaceBasic(BaseModel):
    """DOCSTRING"""

    name: str
    desc: str
    blocks: List[BlockBasic]

class SpaceBasicUtil(BaseModel):
    """DOCSTRING"""

    name: str
    desc: str
    blocks: List[BlockBasicUtil]
    size: int
    used: int

class Space(BaseModel):
    """DOCSTRING"""

    name: str
    desc: str
    blocks: List[Block]

class SpaceExpand(BaseModel):
    """DOCSTRING"""

    name: str
    desc: str
    blocks: List[BlockExpand]

class SpaceUtil(BaseModel):
    """DOCSTRING"""

    name: str
    desc: str
    blocks: List[BlockUtil]
    size: int
    used: int

class SpaceExpandUtil(BaseModel):
    """DOCSTRING"""

    name: str
    desc: str
    blocks: List[BlockExpandUtil]
    size: int
    used: int

class JSONPatch(BaseModel):
    """DOCSTRING"""

    op: str
    path: str
    value: Any

class SpaceUpdate(List[JSONPatch]):
    """DOCSTRING"""

# class SpaceUpdate(BaseModel):
#     """DOCSTRING"""

#     name: Optional[str]
#     desc: Optional[str]
#     # Add limits for name & size length/valid chars

class CIDRReq(BaseModel):
    """DOCSTRING"""

    size: int

class DeleteResvReq(List[str]):
    """DOCSTRING"""

async def scrub_space_patch(patch):
    scrubbed_patch = []

    allowed_ops = [
        {
            "op": "replace",
            "path": "/name",
            "valid": "^([a-zA-Z0-9]){1,16}$",
            "error": "space name can be a maximum of 16 characters and may contain alphanumerics."
        },
        {
            "op": "replace",
            "path": "/desc",
            "valid": "^([a-zA-Z0-9 \._-]){1,32}$",
            "error": "space description can be a maximum of 32 characters and may contain alphanumerics, spaces, underscores, hypens, and periods."
        }
    ]

    for item in list(patch):
        target = next((x for x in allowed_ops if (x['op'] == item['op'] and x['path'] == item['path'])), None)

        if target:
            if re.match(target['valid'], str(item['value'])):
                scrubbed_patch.append(item)
            else:
                raise HTTPException(status_code=400, detail=target['error'])

    return scrubbed_patch

@router.get(
    "",
    response_model = Union[
        List[SpaceExpandUtil],
        List[SpaceExpand],
        List[SpaceUtil],
        List[Space],
        List[SpaceBasicUtil],
        List[SpaceBasic]
    ],
    status_code = 200
)
async def get_spaces(
    expand: bool = False,
    utilization: bool = False,
    authorization: str = Header(None),
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    user_assertion = authorization.split(' ')[1]

    if expand and not is_admin:
        raise HTTPException(status_code=403, detail="Expand parameter can only be used by admins.")

    if expand or utilization:
        vnets = await arg_query(authorization, True, argquery.VNET)

    spaces = await cosmos_query("spaces")

    for space in spaces['spaces']:
        if utilization:
            space['size'] = 0
            space['used'] = 0

        for block in space['blocks']:
            if expand:
                expanded_vnets = []

                for vnet in block['vnets']:
                    target_vnet = next((i for i in vnets if i['id'] == vnet), None)
                    expanded_vnets.append(target_vnet)

                block['vnets'] = expanded_vnets

            if utilization:
                space['size'] += IPNetwork(block['cidr']).size
                block['size'] = IPNetwork(block['cidr']).size
                block['used'] = 0

                for vnet in block['vnets']:
                    if expand:
                        vnet['size'] = 0
                        vnet_prefixes = list(filter(lambda x: IPNetwork(x) in IPNetwork(block['cidr']), vnet['prefixes']))
                    else:
                        target_vnet = next((i for i in vnets if i['id'] == vnet), None)
                        vnet_prefixes = list(filter(lambda x: IPNetwork(x) in IPNetwork(block['cidr']), target_vnet['prefixes']))

                    for prefix in vnet_prefixes:
                        space['used'] += IPNetwork(prefix).size
                        block['used'] += IPNetwork(prefix).size

                        if expand:
                            vnet['size'] += IPNetwork(prefix).size
                            vnet['used'] = 0

                    if expand:
                        for subnet in vnet['subnets']:
                            vnet['used'] += IPNetwork(subnet['prefix']).size
                            subnet['size'] = IPNetwork(subnet['prefix']).size

            if not is_admin:
                user_name = get_username_from_jwt(user_assertion)
                block['resv'] = list(filter(lambda x: x['userId'] == user_name, block['resv']))

    if not is_admin:
        if utilization:
            return [SpaceBasicUtil(**item) for item in spaces['spaces']]
        else:
            return [SpaceBasic(**item) for item in spaces['spaces']]
    else:
        return spaces['spaces']

@router.post(
    "",
    response_model = Space,
    status_code = 201
)
async def create_space(
    space: SpaceReq,
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    current_try = 0
    max_retry = 5

    if not is_admin:
        raise HTTPException(status_code=403, detail="This API is admin restricted.")

    while True:
        try:
            item = await cosmos_query("spaces")

            duplicate = next((x for x in item['spaces'] if x['name'].lower() == space.name.lower()), None)

            if duplicate:
                raise HTTPException(status_code=400, detail="Space name must be unique.")

            new_space = {
                **space.dict(),
                "vnets": [],
                "blocks": []
            }

            item['spaces'].append(jsonable_encoder(new_space))

            await cosmos_upsert("spaces", item)
        except exceptions.CosmosAccessConditionFailedError:
            if current_try < max_retry:
                current_try += 1
                continue
            else:
                raise HTTPException(status_code=500, detail="Error creating space, please try again.")
        else:
            break

    return new_space

@router.get(
    "/{space}",
    response_model = Union[
        SpaceExpandUtil,
        SpaceExpand,
        SpaceUtil,
        Space,
        SpaceBasicUtil,
        SpaceBasic
    ],
    status_code = 200
)
async def get_space(
    space: str,
    expand: bool = False,
    utilization: bool = False,
    authorization: str = Header(None),
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    user_assertion = authorization.split(' ')[1]

    if expand and not is_admin:
        raise HTTPException(status_code=403, detail="Expand parameter can only be used by admins.")

    spaces = await cosmos_query("spaces")

    target_space = next((x for x in spaces['spaces'] if x['name'] == space), None)

    if not target_space:
        raise HTTPException(status_code=400, detail="Invalid space name.")

    if expand or utilization:
        vnets = await arg_query(authorization, is_admin, argquery.VNET)

    if utilization:
        target_space['size'] = 0
        target_space['used'] = 0

    for block in target_space['blocks']:
        if expand:
            expanded_vnets = []

            for vnet in block['vnets']:
                target_vnet = next((i for i in vnets if i['id'] == vnet), None)
                expanded_vnets.append(target_vnet)

            block['vnets'] = expanded_vnets

        if utilization:
            target_space['size'] += IPNetwork(block['cidr']).size
            block['size'] = IPNetwork(block['cidr']).size
            block['used'] = 0

            for vnet in block['vnets']:
                if expand:
                    vnet['size'] = 0
                    vnet_prefixes = list(filter(lambda x: IPNetwork(x) in IPNetwork(block['cidr']), vnet['prefixes']))
                else:
                    target_vnet = next((i for i in vnets if i['id'] == vnet), None)
                    vnet_prefixes = list(filter(lambda x: IPNetwork(x) in IPNetwork(block['cidr']), target_vnet['prefixes']))

                for prefix in vnet_prefixes:
                    target_space['used'] += IPNetwork(prefix).size
                    block['used'] += IPNetwork(prefix).size

                    if expand:
                        vnet['size'] += IPNetwork(prefix).size
                        vnet['used'] = 0

                if expand:
                    for subnet in vnet['subnets']:
                        vnet['used'] += IPNetwork(subnet['prefix']).size
                        subnet['size'] = IPNetwork(subnet['prefix']).size

        if not is_admin:
            user_name = get_username_from_jwt(user_assertion)
            block['resv'] = list(filter(lambda x: x['userId'] == user_name, block['resv']))

    if not is_admin:
        if utilization:
            return SpaceBasicUtil(**target_space)
        else:
            return SpaceBasic(**target_space)
    else:
        return target_space

@router.patch(
    "/{space}",
    response_model = Space,
    status_code = 200
)
async def update_space(
    space: str,
    updates: SpaceUpdate,
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    current_try = 0
    max_retry = 5

    if not is_admin:
        raise HTTPException(status_code=403, detail="This API is admin restricted.")

    while True:
        try:
            item = await cosmos_query("spaces")

            target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

            if not target_space:
                raise HTTPException(status_code=400, detail="Invalid space name.")

            try:
                patch = jsonpatch.JsonPatch(updates)
            except jsonpatch.InvalidJsonPatch:
                raise HTTPException(status_code=500, detail="Invalid JSON patch, please review and try again.")

            scrubbed_patch = jsonpatch.JsonPatch(await scrub_space_patch(patch))
            scrubbed_patch.apply(target_space, in_place = True)

            await cosmos_upsert("spaces", item)
        except exceptions.CosmosAccessConditionFailedError:
            if current_try < max_retry:
                current_try += 1
                continue
            else:
                raise HTTPException(status_code=500, detail="Error updating space, please try again.")
        else:
            break

    return target_space

@router.delete(
    "/{space}",
    status_code = 200
)
async def delete_space(
    space: str,
    force: Optional[bool] = False,
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    current_try = 0
    max_retry = 5

    if not is_admin:
        raise HTTPException(status_code=403, detail="This API is admin restricted.")

    while True:
        try:
            item = await cosmos_query("spaces")

            target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

            if not target_space:
                raise HTTPException(status_code=400, detail="Invalid space name.")

            if not force:
                if len(target_space['blocks']) > 0:
                    raise HTTPException(status_code=400, detail="Cannot delete space while it contains blocks.")

            index = next((i for i, item in enumerate(item['spaces']) if item['name'] == space), None)
            del item['spaces'][index]

            await cosmos_upsert("spaces", item)
        except exceptions.CosmosAccessConditionFailedError:
            if current_try < max_retry:
                current_try += 1
                continue
            else:
                raise HTTPException(status_code=500, detail="Error deleting space, please try again.")
        else:
            break

    return item['spaces']

@router.get(
    "/{space}/blocks",
    response_model = Union[
        List[BlockExpandUtil],
        List[BlockExpand],
        List[BlockUtil],
        List[Block],
        List[BlockBasicUtil],
        List[BlockBasic]
    ],
    status_code = 200
)
async def get_blocks(
    space: str,
    expand: bool = False,
    utilization: bool = False,
    authorization: str = Header(None),
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    user_assertion = authorization.split(' ')[1]

    if expand and not is_admin:
        raise HTTPException(status_code=403, detail="Expand parameter can only be used by admins.")

    spaces = await cosmos_query("spaces")

    target_space = next((x for x in spaces['spaces'] if x['name'] == space), None)

    if not target_space:
        raise HTTPException(status_code=400, detail="Invalid space name.")

    block_list = target_space['blocks']

    if expand or utilization:
        vnets = await arg_query(authorization, is_admin, argquery.VNET)

    for block in block_list:
        if expand:
            expanded_vnets = []

            for vnet in block['vnets']:
                target_vnet = next((i for i in vnets if i['id'] == vnet), None)
                expanded_vnets.append(target_vnet)

            block['vnets'] = expanded_vnets

        if utilization:
            block['size'] = IPNetwork(block['cidr']).size
            block['used'] = 0

            for vnet in block['vnets']:
                if expand:
                    vnet['size'] = 0
                    vnet_prefixes = list(filter(lambda x: IPNetwork(x) in IPNetwork(block['cidr']), vnet['prefixes']))
                else:
                    target_vnet = next((i for i in vnets if i['id'] == vnet), None)
                    vnet_prefixes = list(filter(lambda x: IPNetwork(x) in IPNetwork(block['cidr']), target_vnet['prefixes']))

                for prefix in vnet_prefixes:
                    block['used'] += IPNetwork(prefix).size

                    if expand:
                        vnet['size'] += IPNetwork(prefix).size
                        vnet['used'] = 0

                if expand:
                    for subnet in vnet['subnets']:
                        vnet['used'] += IPNetwork(subnet['prefix']).size
                        subnet['size'] = IPNetwork(subnet['prefix']).size

        if not is_admin:
            user_name = get_username_from_jwt(user_assertion)
            block['resv'] = list(filter(lambda x: x['userId'] == user_name, block['resv']))

    if not is_admin:
        if utilization:
            return [BlockBasicUtil(**item) for item in target_space['blocks']]
        else:
            return [BlockBasic(**item) for item in target_space['blocks']]
    else:
        return target_space['blocks']

@router.post(
    "/{space}/blocks",
    response_model = Block,
    status_code = 201
)
async def create_block(
    space: str,
    block: BlockReq,
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    current_try = 0
    max_retry = 5

    if not is_admin:
        raise HTTPException(status_code=403, detail="This API is admin restricted.")

    while True:
        try:
            item = await cosmos_query("spaces")
            target = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

            if not target:
                raise HTTPException(status_code=400, detail="Invalid space name.")

            block_cidrs = IPSet([x['cidr'] for x in target['blocks']])

            overlap = bool(IPSet([str(block.cidr)]) & block_cidrs)

            if overlap:
                raise HTTPException(status_code=400, detail="New block cannot overlap existing blocks.")

            new_block = {
                **block.dict(),
                "vnets": [],
                "resv": []
            }

            target['blocks'].append(jsonable_encoder(new_block))

            await cosmos_upsert("spaces", item)
        except exceptions.CosmosAccessConditionFailedError:
            if current_try < max_retry:
                current_try += 1
                continue
            else:
                raise HTTPException(status_code=500, detail="Error creating block, please try again.")
        else:
            break

    return new_block

@router.get(
    "/{space}/blocks/{block}",
    response_model = Union[
        BlockExpandUtil,
        BlockExpand,
        BlockUtil,
        Block,
        BlockBasicUtil,
        BlockBasic
    ],
    status_code = 200
)
async def get_block(
    space: str,
    block: str,
    expand: bool = False,
    utilization: bool = False,
    authorization: str = Header(None),
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    user_assertion = authorization.split(' ')[1]

    if expand and not is_admin:
        raise HTTPException(status_code=403, detail="Expand parameter can only be used by admins.")

    item = await cosmos_query("spaces")

    target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

    if not target_space:
        raise HTTPException(status_code=400, detail="Invalid space name.")

    target_block = next((x for x in target_space['blocks'] if x['name'].lower() == block.lower()), None)

    if not target_block:
        raise HTTPException(status_code=400, detail="Invalid block name.")

    if expand or utilization:
        vnets = await arg_query(authorization, is_admin, argquery.VNET)


    if expand:
        expanded_vnets = []

        for vnet in target_block['vnets']:
            target_vnet = next((i for i in vnets if i['id'] == vnet), None)
            expanded_vnets.append(target_vnet)

        target_block['vnets'] = expanded_vnets

    if utilization:
        target_block['size'] = IPNetwork(target_block['cidr']).size
        target_block['used'] = 0

        for vnet in target_block['vnets']:
            if expand:
                vnet['size'] = 0
                vnet_prefixes = list(filter(lambda x: IPNetwork(x) in IPNetwork(target_block['cidr']), vnet['prefixes']))
            else:
                target_vnet = next((i for i in vnets if i['id'] == vnet), None)
                vnet_prefixes = list(filter(lambda x: IPNetwork(x) in IPNetwork(target_block['cidr']), target_vnet['prefixes']))

            for prefix in vnet_prefixes:
                target_block['used'] += IPNetwork(prefix).size

                if expand:
                    vnet['size'] += IPNetwork(prefix).size
                    vnet['used'] = 0

            if expand:
                for subnet in vnet['subnets']:
                    vnet['used'] += IPNetwork(subnet['prefix']).size
                    subnet['size'] = IPNetwork(subnet['prefix']).size

    if not is_admin:
        user_name = get_username_from_jwt(user_assertion)
        target_block['resv'] = list(filter(lambda x: x['userId'] == user_name, target_block['resv']))

    if not is_admin:
        if utilization:
            return BlockBasicUtil(**target_block)
        else:
            return BlockBasic(**target_block)
    else:
        return target_block

@router.delete(
    "/{space}/blocks/{block}",
    status_code = 200
)
async def delete_block(
    space: str,
    block: str,
    force: Optional[bool] = False,
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    current_try = 0
    max_retry = 5

    if not is_admin:
        raise HTTPException(status_code=403, detail="This API is admin restricted.")

    while True:
        try:
            item = await cosmos_query("spaces")

            target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

            if not target_space:
                raise HTTPException(status_code=400, detail="Invalid space name.")

            target_block = next((x for x in target_space['blocks'] if x['name'].lower() == block.lower()), None)

            if not target_block:
                raise HTTPException(status_code=400, detail="Invalid block name.")

            if not force:
                if len(target_block['vnets']) > 0 or len(target_block['resv']) > 0:
                    raise HTTPException(status_code=400, detail="Cannot delete block while it contains vNets or reservations.")

            index = next((i for i, item in enumerate(target_space['blocks']) if item['name'] == block), None)
            del target_space['blocks'][index]

            await cosmos_upsert("spaces", item)
        except exceptions.CosmosAccessConditionFailedError:
            if current_try < max_retry:
                current_try += 1
                continue
            else:
                raise HTTPException(status_code=500, detail="Error deleting block, please try again.")
        else:
            break

    return target_space

@router.get(
    "/{space}/blocks/{block}/available",
    response_model = Union[
        List[VNetExpand],
        List[str]
    ],
    status_code = 200
)
async def available_block_vnets(
    space: str,
    block: str,
    expand: bool = False,
    authorization: str = Header(None),
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    available_vnets = []

    if not is_admin:
        raise HTTPException(status_code=403, detail="API restricted to admins.")

    item = await cosmos_query("spaces")

    target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

    if not target_space:
        raise HTTPException(status_code=400, detail="Invalid space name.")

    target_block = next((x for x in target_space['blocks'] if x['name'].lower() == block.lower()), None)

    if not target_block:
        raise HTTPException(status_code=400, detail="Invalid block name.")

    vnet_list = await arg_query(authorization, True, argquery.VNET)

    for vnet in vnet_list:
        valid = list(filter(lambda x: IPNetwork(x) in IPNetwork(target_block['cidr']), vnet['prefixes']))

        if valid:
            vnet['prefixes'] = valid
            available_vnets.append(vnet)

    # ADD CHECK TO MAKE SURE VNET ISN'T ASSIGNED TO ANOTHER BLOCK
    # assigned_vnets = [''.join(vnet) for space in item['spaces'] for block in space['blocks'] for vnet in block['vnets']]
    # unassigned_vnets = list(set(available_vnets) - set(assigned_vnets)) + list(set(assigned_vnets) - set(available_vnets))

    for space_iter in item['spaces']:
        for block_iter in space_iter['blocks']:
            for vnet_iter in block_iter['vnets']:
                if space_iter['name'] != space and block_iter['name'] != block:
                    vnet_index = next((i for i, item in enumerate(available_vnets) if item['id'] == vnet_iter), None)

                    if vnet_index:
                        del available_vnets[vnet_index]

    if expand:
        return available_vnets
    else:
        return [item['id'] for item in available_vnets]

@router.get(
    "/{space}/blocks/{block}/networks",
    response_model = Union[
        List[VNetExpand],
        List[str]
    ],
    status_code = 200
)
async def available_block_vnets(
    space: str,
    block: str,
    expand: bool = False,
    authorization: str = Header(None),
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    block_vnets = []

    if not is_admin:
        raise HTTPException(status_code=403, detail="API restricted to admins.")

    item = await cosmos_query("spaces")

    target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

    if not target_space:
        raise HTTPException(status_code=400, detail="Invalid space name.")

    target_block = next((x for x in target_space['blocks'] if x['name'].lower() == block.lower()), None)

    if not target_block:
        raise HTTPException(status_code=400, detail="Invalid block name.")

    if expand:
        vnet_list = await arg_query(authorization, True, argquery.VNET)

        for block_vnet in target_block['vnets']:
            target_vnet = next((x for x in vnet_list if x['id'].lower() == block_vnet.lower()), None)
            block_vnets.append(target_vnet)

        return block_vnets
    else:
        return target_block['vnets']

@router.post(
    "/{space}/blocks/{block}/networks",
    response_model = BlockRes,
    status_code = 201
)
async def create_block_vnet(
    space: str,
    block: str,
    vnet: VNet,
    authorization: str = Header(None),
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    current_try = 0
    max_retry = 5

    if not is_admin:
        raise HTTPException(status_code=403, detail="API restricted to admins.")

    while True:
        try:
            item = await cosmos_query("spaces")

            target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

            if not target_space:
                raise HTTPException(status_code=400, detail="Invalid space name.")

            target_block = next((x for x in target_space['blocks'] if x['name'].lower() == block.lower()), None)

            if not target_block:
                raise HTTPException(status_code=400, detail="Invalid block name.")

            vnet_list = await arg_query(authorization, True, argquery.VNET)

            target_vnet = next((x for x in vnet_list if x['id'].lower() == vnet.id.lower()), None)

            if not target_vnet:
                raise HTTPException(status_code=400, detail="Invalid vNet ID.")

            target_cidr = next((x for x in target_vnet['prefixes'] if IPNetwork(x) in IPNetwork(target_block['cidr'])), None)

            if not target_cidr:
                raise HTTPException(status_code=400, detail="vNet CIDR not within Block CIDR.")

            block_vnet_cidrs = []

            for v in target_block['vnets']:
                target = next((x for x in vnet_list if x['id'].lower() == v.lower()), None)
                prefixes = list(filter(lambda x: IPNetwork(x) in IPNetwork(target_block['cidr']), target['prefixes']))
                block_vnet_cidrs += prefixes

            cidr_overlap = IPSet(block_vnet_cidrs) & IPSet([target_cidr])

            if cidr_overlap:
                raise HTTPException(status_code=400, detail="Block already contains vNet(s) within the CIDR range of target vNet.")

            target_block['vnets'].append(vnet.id)

            await cosmos_upsert("spaces", item)
        except exceptions.CosmosAccessConditionFailedError:
            if current_try < max_retry:
                current_try += 1
                continue
            else:
                raise HTTPException(status_code=500, detail="Error adding vNet to block, please try again.")
        else:
            break

    return target_block

# THE REQUEST BODY ITEM SHOULD MATCH THE BLOCK VALUE THAT IS BEING PATCHED
@router.put(
    "/{space}/blocks/{block}/networks",
    response_model = List[str],
    status_code = 200
)
async def update_block_vnets(
    space: str,
    block: str,
    vnets: VNetsUpdate,
    authorization: str = Header(None),
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    current_try = 0
    max_retry = 5

    if not is_admin:
        raise HTTPException(status_code=403, detail="API restricted to admins.")

    while True:
        try:
            item = await cosmos_query("spaces")

            target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

            if not target_space:
                raise HTTPException(status_code=400, detail="Invalid space name.")

            target_block = next((x for x in target_space['blocks'] if x['name'].lower() == block.lower()), None)

            if not target_block:
                raise HTTPException(status_code=400, detail="Invalid block name.")

            unique_vnets = len(vnets) == len(set(vnets))

            if not unique_vnets:
                raise HTTPException(status_code=400, detail="List contains duplicate vNets.")

            vnet_list = await arg_query(authorization, True, argquery.VNET)

            invalid_vnets = []
            outside_block_cidr = []
            vnet_ipset = IPSet([])
            vnet_overlap = False

            for v in vnets:
                target_vnet = next((x for x in vnet_list if x['id'].lower() == v.lower()), None)

                if not target_vnet:
                    invalid_vnets.append(v)
                else:
                    target_cidr = next((x for x in target_vnet['prefixes'] if IPNetwork(x) in IPNetwork(target_block['cidr'])), None)

                    if not target_cidr:
                        outside_block_cidr.append(v)
                    else:
                        if not vnet_ipset & IPSet([target_cidr]):
                            vnet_ipset.add(target_cidr)
                        else:
                            vnet_overlap = True

            if vnet_overlap:
                raise HTTPException(status_code=400, detail="vNet list contains overlapping CIDRs.")

            if len(outside_block_cidr) > 0:
                raise HTTPException(status_code=400, detail="vNet CIDR(s) not within Block CIDR: {}".format(outside_block_cidr))

            if len(invalid_vnets) > 0:
                raise HTTPException(status_code=400, detail="Invalid vNet ID(s): {}".format(invalid_vnets))

            target_block['vnets'] = vnets

            await cosmos_upsert("spaces", item)
        except exceptions.CosmosAccessConditionFailedError:
            if current_try < max_retry:
                current_try += 1
                continue
            else:
                raise HTTPException(status_code=500, detail="Error updating block vNets, please try again.")
        else:
            break

    return target_block['vnets']

@router.delete(
    "/{space}/blocks/{block}/networks",
    response_model = BlockRes,
    status_code = 200
)
async def delete_block_vnets(
    space: str,
    block: str,
    req: VNets,
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    current_try = 0
    max_retry = 5

    if not is_admin:
        raise HTTPException(status_code=403, detail="API restricted to admins.")

    while True:
        try:
            item = await cosmos_query("spaces")

            target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

            if not target_space:
                raise HTTPException(status_code=400, detail="Invalid space name.")

            target_block = next((x for x in target_space['blocks'] if x['name'].lower() == block.lower()), None)

            if not target_block:
                raise HTTPException(status_code=400, detail="Invalid block name.")

            unique_vnets = len(set(req.ids)) == len(req.ids)

            if not unique_vnets:
                raise HTTPException(status_code=400, detail="List contains one or more duplicate vNet id's.")

            current_vnets = list(x for x in target_block['vnets'])
            ids_exist = all(elem in current_vnets for elem in req.ids)

            if not ids_exist:
                raise HTTPException(status_code=400, detail="List contains one or more invalid vNet id's.")
                # OR VNET IDS THAT DON'T BELONG TO THE CURRENT BLOCK

            for id in req.ids:
                index = next((i for i, item in enumerate(target_block['vnets']) if item == id), None)
                del target_block['vnets'][index]

            await cosmos_upsert("spaces", item)
        except exceptions.CosmosAccessConditionFailedError:
            if current_try < max_retry:
                current_try += 1
                continue
            else:
                raise HTTPException(status_code=500, detail="Error removing block vNet(s), please try again.")
        else:
            break

    return target_block

@router.get(
    "/{space}/blocks/{block}/reservations",
    response_model = List[Reservation],
    status_code = 200
)
async def get_block_reservations(
    space: str,
    block: str,
    authorization: str = Header(None),
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    user_assertion = authorization.split(' ')[1]

    item = await cosmos_query("spaces")

    target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

    if not target_space:
        raise HTTPException(status_code=400, detail="Invalid space name.")

    target_block = next((x for x in target_space['blocks'] if x['name'].lower() == block.lower()), None)

    if not target_block:
        raise HTTPException(status_code=400, detail="Invalid block name.")

    if not is_admin:
        user_name = get_username_from_jwt(user_assertion)
        return list(filter(lambda x: x['userId'] == user_name, target_block['resv']))
    else:
        return target_block['resv']

@router.post(
    "/{space}/blocks/{block}/reservations",
    response_model = Reservation,
    status_code = 201
)
async def create_block_reservation(
    space: str,
    block: str,
    req: CIDRReq,
    authorization: str = Header(None)
):
    """DOCSTRING"""

    user_assertion = authorization.split(' ')[1]
    decoded = jwt.decode(user_assertion, options={"verify_signature": False})

    current_try = 0
    max_retry = 5

    while True:
        try:
            item = await cosmos_query("spaces")

            target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

            if not target_space:
                raise HTTPException(status_code=400, detail="Invalid space name.")

            target_block = next((x for x in target_space['blocks'] if x['name'].lower() == block.lower()), None)

            if not target_block:
                raise HTTPException(status_code=400, detail="Invalid block name.")

            vnet_list = await arg_query(authorization, True, argquery.VNET)

            block_all_cidrs = []

            for v in target_block['vnets']:
                target = next((x for x in vnet_list if x['id'].lower() == v.lower()), None)
                prefixes = list(filter(lambda x: IPNetwork(x) in IPNetwork(target_block['cidr']), target['prefixes']))
                block_all_cidrs += prefixes

            for r in target_block['resv']:
                block_all_cidrs.append(r['cidr'])

            block_set = IPSet([target_block['cidr']])
            reserved_set = IPSet(block_all_cidrs)
            available_set = block_set ^ reserved_set

            available_block = next((net for net in list(available_set.iter_cidrs()) if net.prefixlen <= req.size), None)

            if not available_block:
                raise HTTPException(status_code=500, detail="Subnet of requested size unavailable in target block.")

            next_cidr = list(available_block.subnet(req.size))[0]

            if "preferred_username" in decoded:
                creator_id = decoded["preferred_username"]
            else:
                creator_id = f"spn:{decoded['oid']}"

            new_cidr = {
                "id": shortuuid.uuid(),
                "cidr": str(next_cidr),
                "userId": creator_id,
                "createdOn": (time.time() * 1000),
                "status": "wait"
            }

            target_block['resv'].append(new_cidr)

            # NEED TO RETURN GUID FOR USER TO APPEND TO AZURE TAG ON VNET
    
            await cosmos_upsert("spaces", item)
        except exceptions.CosmosAccessConditionFailedError:
            if current_try < max_retry:
                current_try += 1
                continue
            else:
                raise HTTPException(status_code=500, detail="Error creating block reservation, please try again.")
        else:
            break

    return new_cidr

@router.delete(
    "/{space}/blocks/{block}/reservations",
    status_code = 200
)
async def delete_block_reservations(
    space: str,
    block: str,
    req: DeleteResvReq,
    authorization: str = Header(None),
    is_admin: str = Depends(get_admin)
):
    """DOCSTRING"""

    current_try = 0
    max_retry = 5

    user_assertion = authorization.split(' ')[1]
    user_name = get_username_from_jwt(user_assertion)

    while True:
        try:
            item = await cosmos_query("spaces")

            target_space = next((x for x in item['spaces'] if x['name'].lower() == space.lower()), None)

            if not target_space:
                raise HTTPException(status_code=400, detail="Invalid space name.")

            target_block = next((x for x in target_space['blocks'] if x['name'].lower() == block.lower()), None)

            if not target_block:
                raise HTTPException(status_code=400, detail="Invalid block name.")

            unique_ids = len(set(req)) == len(req)

            if not unique_ids:
                raise HTTPException(status_code=400, detail="List contains one or more duplicate id's.")

            current_reservations = list(o['id'] for o in target_block['resv'])
            ids_exist = all(elem in current_reservations for elem in req)

            if not ids_exist:
                raise HTTPException(status_code=400, detail="List contains one or more invalid id's.")

            if not is_admin:
                not_owned = list(filter(lambda x: x['id'] in req and x['userId'] != user_name, target_block['resv']))

                if not_owned:
                    raise HTTPException(status_code=403, detail="Users can only delete their own reservations.")

            for id in req:
                index = next((i for i, item in enumerate(target_block['resv']) if item['id'] == id), None)
                del target_block['resv'][index]

            await cosmos_upsert("spaces", item)
        except exceptions.CosmosAccessConditionFailedError:
            if current_try < max_retry:
                current_try += 1
                continue
            else:
                raise HTTPException(status_code=500, detail="Error removing block reservation(s), please try again.")
        else:
            break

    return target_block
