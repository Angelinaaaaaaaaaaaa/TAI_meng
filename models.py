"""Pydantic schemas and dataclasses shared across the rearrangement pipeline."""

from dataclasses import dataclass
from typing import List

from pydantic import BaseModel


class FileDescription(BaseModel):
    relative_folder_path: str
    file: str
    description: str


class BackboneResult(BaseModel):
    backbone_path: str


class BackboneGroup(BaseModel):
    group_name: str
    main_item: str
    related_items: List[str]
    description: str


class BackboneGroupsResponse(BaseModel):
    groups: List[BackboneGroup]


class RearrangedGroup(BaseModel):
    group_name: str
    main_item: str
    related_items: List[str]


class OrphanMatch(BaseModel):
    item_path: str
    assigned_group: str


class OrphanMatchResponse(BaseModel):
    matches: List[OrphanMatch]


class MiscGroupAssignment(BaseModel):
    item_path: str
    new_group_name: str
    new_group_description: str


class MiscRefinementResponse(BaseModel):
    assignments: List[MiscGroupAssignment]


class AggregationDecision(BaseModel):
    paths_to_aggregate: List[str]


@dataclass(frozen=True)
class PipelineContext:
    base_dir: str
    course_name: str
    output_dir: str
    log_dir: str
    multi_match: bool
