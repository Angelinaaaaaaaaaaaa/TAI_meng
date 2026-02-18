# BFS v2 — Reorganization Report

Generated: 2026-02-17 16:39:56

## 1. Summary

| Metric | Value |
|--------|------:|
| Files on disk | 2633 |
| Classified as **study** | 866 |
| Classified as **practice** | 1387 |
| Classified as **support** | 479 |
| Classified as **skip** | 0 |
| Files classified via folder | 2562 |
| Files classified individually | 68 |
| Total file mappings | 2630 |
| Skipped folders | 0 |
| Files not in DB | 2134 |
| Stale DB entries (no file) | 0 |
| LLM calls | 173 |

## 2. Folder Decisions

| Folder | Category | Conf | Mixed | Description |
|--------|----------|-----:|:-----:|-------------|
| `articles` | support | 0.66 | Y | A set of course resource articles (policies, setup/tools guides, and language re |
| `assets` | study | 0.90 |  | Repository of CS 61A lecture slide PDFs and accompanying lecture/demo code scrip |
| `assignment-calendar` | support | 0.87 |  | Course website content that links students to an assignment due-date calendar th |
| `disc` | study | 0.93 |  | Discussion section handouts/worksheets (and related metadata) for CS 61A topics  |
| `exam` | support | 0.88 |  | Archive of past midterms/finals and their solutions across semesters for exam pr |
| `hw` | practice | 0.96 |  | Homework assignments (with starter files/zips) and their solution sets for stude |
| `instructor` | support | 0.86 |  | Course website content that introduces the instructors and provides staff-relate |
| `lab` | practice | 0.93 |  | Lab assignment handouts, starter code bundles, and corresponding solution walkth |
| `lecture` | support | 0.78 |  | Course homepage and lecture schedule/announcements used to navigate the class ti |
| `office-hours` | support | 0.87 |  | Course logistics web content for office hours and an assignment calendar link to |
| `proj` | practice | 0.88 |  | Programming project assignments (specs, starter code, tests, and assets) for CS  |
| `resources` | support | 0.90 |  | Course homepage and schedule page used for overall course navigation, announceme |
| `staff` | support | 0.94 |  | Course staff directory page and metadata for student reference and contact infor |
| `study-guide` | study | 0.93 |  | A supplemental study guide webpage (with metadata) for reviewing orders of growt |
| `textbook` | support | 0.93 |  | Web-based textbook/reference (Composing Programs) for students to read and use a |
| `youtube` | study | 0.62 | Y | A large repository of CS61A YouTube/recorded videos (lectures, discussion/lab wa |
| `youtube/61A Fall 2023 Lecture 32` | study | 0.98 |  | Lecture 32 video recordings (with metadata) for students to watch and review con |
| `youtube/Aggregation and Databases (Su25)` | study | 0.93 |  | Lecture videos (with metadata) covering SQL aggregation, grouping, table managem |
| `youtube/CS 61A Fall 2016 Midterm 1` | practice | 0.88 |  | Video walkthroughs explaining solutions to CS61A Fall 2016 Midterm 1 questions,  |
| `youtube/CS 61A Fall 2016 Midterm 2` | study | 0.82 |  | Recorded Midterm 2 question walkthrough video (with metadata) for students to wa |
| `youtube/CS 61A Fall 2017 Final` | practice | 0.86 |  | Video walkthrough solutions for each question on the CS61A Fall 2017 final exam, |
| `youtube/CS 61A Fall 2017 Midterm 1` | practice | 0.86 |  | Recorded walkthrough videos (with metadata) explaining solutions to each questio |
| `youtube/CS 61A Fall 2017 Midterm 2` | practice | 0.90 |  | Video walkthroughs (with metadata) explaining solutions to each question on CS 6 |
| `youtube/CS 61A Fall 2018 Final` | practice | 0.92 |  | Video walkthroughs/solutions for each question of the CS61A Fall 2018 final exam |
| `youtube/CS 61A Fall 2018 Midterm 1` | practice | 0.90 |  | Video walkthroughs of CS61A Fall 2018 Midterm 1 questions with metadata for each |
| `youtube/CS 61A Fall 2018 Midterm 2` | practice | 0.90 |  | Videos walking through CS61A Fall 2018 Midterm 2 questions (exam problem solutio |
| `youtube/CS 61A Fall 2020： Midterm 1 Walkthrough` | practice | 0.87 |  | Recorded videos and metadata providing question-by-question walkthrough solution |
| `youtube/CS 61A Fall 2020： Midterm 2 Walkthrough` | practice | 0.92 |  | Video walkthroughs explaining solutions to CS61A Fall 2020 Midterm 2 questions f |
| `youtube/CS 61A Fall 2021 Final Walkthrough` | practice | 0.92 |  | Video walkthroughs explaining solutions to each question part of the CS61A Fall  |
| `youtube/CS 61A Spring 2016 Final` | practice | 0.86 |  | Video walkthroughs of the CS 61A Spring 2016 final exam questions (with accompan |
| `youtube/CS 61A Spring 2017 Final` | practice | 0.90 |  | Video walkthroughs (with metadata) explaining solutions to each question on the  |
| `youtube/CS 61A Spring 2017 Midterm 2 Walkthrough` | practice | 0.90 |  | Video walkthrough solutions for CS61A Spring 2017 Midterm 2 problems with accomp |
| `youtube/CS 61A Spring 2018 Final Walkthrough` | practice | 0.93 |  | Video walkthrough solutions for each question on the CS61A Spring 2018 final exa |
| `youtube/CS 61A Spring 2018 Midterm 1` | practice | 0.86 |  | Video walkthroughs for CS61A Spring 2018 Midterm 1 problems with accompanying me |
| `youtube/CS 61A Spring 2018 Midterm 2` | practice | 0.90 |  | Video walkthroughs of CS61A Spring 2018 Midterm 2 problems with accompanying met |
| `youtube/CS 61A Spring 2019 Midterm 1` | practice | 0.86 |  | Video walkthroughs of CS61A Spring 2019 Midterm 1 questions with accompanying me |
| `youtube/CS 61A Spring 2019 Midterm 2` | practice | 0.86 |  | Videos and metadata for Midterm 2 question-by-question walkthroughs/solutions fo |
| `youtube/CS 61A Spring 2022 Final Walkthrough` | practice | 0.86 |  | Video walkthrough solutions for each question on the CS 61A Spring 2022 final ex |
| `youtube/CS 61A Summer 2017 Final Walkthrough` | practice | 0.90 |  | Video walkthroughs and metadata explaining solutions to questions from the CS61A |
| `youtube/CS 61A Summer 2018 Final` | practice | 0.83 |  | Video walkthroughs of specific questions from the CS 61A Summer 2018 final exam, |
| `youtube/CS 61A Summer 2018 Mock Final Walkthrough` | practice | 0.86 |  | Video walkthroughs explaining solutions to each question of the CS61A Summer 201 |
| `youtube/CS 61A Summer 2022 Final Walkthrough` | practice | 0.90 |  | Video walkthrough solutions for each question on the CS61A Summer 2022 final exa |
| `youtube/CS walkthrough vids` | practice | 0.90 |  | Video walkthroughs (with metadata) explaining solutions to CS61A Fall 2016 final |
| `youtube/CS61A Fall 2019 Final` | practice | 0.90 |  | Video walkthroughs/solutions for each question of the CS61A Fall 2019 final exam |
| `youtube/CS61A Fall 2019 Midterm 1` | practice | 0.92 |  | Midterm 1 question-by-question walkthrough videos (with metadata) for CS61A Fall |
| `youtube/CS61A Fall 2019 Midterm 2` | practice | 0.90 |  | Recorded walkthrough videos (with metadata) explaining solutions to CS61A Fall 2 |
| `youtube/CS61A Fall 2021 Midterm 1 Walkthrough` | practice | 0.91 |  | Video walkthroughs explaining solutions to each question on the CS61A Fall 2021  |
| `youtube/CS61A Fall 2021 Midterm 2` | practice | 0.92 |  | Video walkthroughs (with metadata) explaining solutions to CS61A Fall 2021 Midte |
| `youtube/CS61A Spring 2019 Final` | practice | 0.87 |  | Video walkthroughs of each question from the CS61A Spring 2019 final exam (with  |
| `youtube/CS61A Spring 2020 Midterm 1` | practice | 0.90 |  | Videos and metadata providing question-by-question walkthroughs/solutions for CS |
| `youtube/CS61A Spring 2022 Midterm 2 Walkthrough` | practice | 0.93 |  | Video walkthrough solutions for each question on CS61A Spring 2022 Midterm 2, wi |
| `youtube/Control` | study | 0.98 |  | Lecture videos (with metadata) teaching Python control flow and related topics f |
| `youtube/Data Abstraction and ADT Trees (Su25)` | study | 0.96 |  | Lecture video recordings (with metadata) teaching data abstraction and tree ADTs |
| `youtube/Disc 07` | study | 0.90 |  | Recorded discussion and lab walkthrough videos (with metadata) for students to w |
| `youtube/Discussion 10： Tail Calls, Scheme Data Abstractions, SQL` | study | 0.93 |  | Discussion section walkthrough videos (with metadata) covering tail calls, Schem |
| `youtube/Discussion 4： Tree Recursion, Trees, Lists` | study | 0.90 |  | Discussion/lecture-style walkthrough videos (with metadata) teaching tree recurs |
| `youtube/Discussion 5： Iterators, Generators, Efficiency` | study | 0.74 | Y | Recorded discussion-section (and a related exam) walkthrough videos on iterators |
| `youtube/Discussion 9： Interpreters` | study | 0.94 |  | Discussion walkthrough videos (with metadata) teaching interpreter concepts and  |
| `youtube/Efficiency` | study | 0.98 |  | Lecture videos (with metadata) teaching efficiency concepts like memoization, or |
| `youtube/Environments` | study | 0.97 |  | Lecture video recordings (with metadata) covering Python environments, scope, an |
| `youtube/Environments (Su25)` | study | 0.94 |  | Video lecture segments (with metadata) covering environment diagrams and higher- |
| `youtube/Final Review (Su25)` | study | 0.93 |  | Recorded final review lecture segments on trees and recursion with accompanying  |
| `youtube/Functions` | study | 0.98 |  | Topic-focused CS61A lecture videos (with metadata) teaching core concepts about  |
| `youtube/Higher-Order Functions` | study | 0.94 |  | Recorded lecture videos (with metadata) covering iteration, control, and higher- |
| `youtube/Inheritance and Representation (Su25)` | study | 0.94 |  | Recorded lecture segments for the Inheritance and Representation unit (Su25), wi |
| `youtube/Interpreters (Su25)` | study | 0.98 |  | Lecture video recordings (with metadata) for the Interpreters unit in CS61A Summ |
| `youtube/Iterators and Generators (Su25)` | study | 0.96 |  | Video lecture materials (with metadata) covering iterators and generators in Pyt |
| `youtube/Linked Lists (Su25)` | study | 0.95 |  | Lecture video recordings (with metadata) covering linked lists, including proces |
| `youtube/Midterm Review (Su25)` | study | 0.93 |  | Recorded midterm review and concept lecture videos (with metadata) for students  |
| `youtube/Mutability (Su25)` | study | 0.97 |  | Recorded lecture videos and metadata for the CS61A Mutability unit (Su25) for st |
| `youtube/Mutable Trees (Su25)` | study | 0.96 |  | Lecture recordings (with metadata) covering mutable tree data structures and mut |
| `youtube/Objects and Attributes (Su25)` | study | 0.98 |  | Lecture video recordings and metadata for the CS61A ‘Objects and Attributes’ top |
| `youtube/Recursion` | study | 0.95 |  | Topic-organized lecture videos (with metadata) teaching recursion concepts for s |
| `youtube/SQL and Tables (Su25)` | study | 0.98 |  | Lecture video recordings (with metadata) covering the SQL and tables unit for CS |
| `youtube/Scheme` | study | 0.97 |  | Recorded lecture segments on Scheme concepts (special forms, lambdas, interprete |
| `youtube/Scheme Lists` | study | 0.94 |  | Recorded lecture/discussion videos (with metadata) teaching Scheme list concepts |
| `youtube/Sequences and Containers (Su25)` | study | 0.97 |  | Lecture video recordings (with metadata) covering sequences and containers topic |
| `youtube/Tail Calls (Su25)` | study | 0.95 |  | Lecture video recordings (with metadata) teaching tail calls/tail recursion and  |
| `youtube/Tree Recursion` | study | 0.93 |  | Topic-focused lecture recordings (with metadata) teaching tree recursion concept |
| `youtube/[CS 61A FA22] Midterm 1 Walkthrough` | practice | 0.90 |  | Video walkthroughs explaining the solutions to each question on the CS61A FA22 M |
| `youtube/[CS 61A FA22] Midterm 2 Walkthrough Videos` | practice | 0.93 |  | Midterm 2 question-by-question walkthrough videos (with metadata) for students t |
| `youtube/[CS 61A FA23] Final Walkthrough` | practice | 0.86 |  | Video walkthroughs explaining solutions to each problem on the CS61A Fall 2023 f |
| `youtube/[CS 61A FA23] Midterm 1 Walkthrough` | practice | 0.86 |  | Recorded solution walkthrough videos for CS61A Fall 2023 Midterm 1 problems, wit |
| `youtube/[CS 61A FA23] Midterm 2 Walkthrough Videos` | practice | 0.86 |  | Midterm 2 question-by-question walkthrough videos (with metadata) that explain e |
| `youtube/[CS 61A FA24] Midterm 1 Walkthrough` | practice | 0.90 |  | Problem-by-problem video walkthroughs (with metadata) explaining solutions to CS |
| `youtube/[CS 61A FA24] Midterm 2 Walkthrough` | practice | 0.86 |  | Recorded walkthrough videos explaining solutions to each problem on CS61A Fall 2 |
| `youtube/[CS 61A SP23] Final Walkthrough` | practice | 0.86 |  | Video walkthroughs explaining solutions to each problem on the CS61A SP23 final  |
| `youtube/[CS 61A SP23] Midterm 1 Walkthrough` | practice | 0.86 |  | Recorded video walkthroughs explaining solutions to each problem on CS61A SP23 M |
| `youtube/[CS 61A SP23] Midterm 2 Walkthrough` | practice | 0.95 |  | Recorded videos that walk through and explain solutions to each problem on CS61A |
| `youtube/[CS 61A SP24] Mega Discussion 08` | study | 0.97 |  | Discussion 8 walkthrough videos (with metadata) for reviewing linked list proble |
| `youtube/[CS 61A SP24] Midterm 1 Walkthrough` | practice | 0.93 |  | Recorded walkthrough videos (with metadata) explaining solutions to CS61A SP24 M |
| `youtube/[CS 61A SP24] Midterm 2 Walkthrough` | practice | 0.90 |  | Video walkthroughs explaining solutions to each CS61A Spring 2024 Midterm 2 prob |
| `youtube/[CS 61A SP25] Midterm 1 Walkthrough` | practice | 0.86 |  | Recorded walkthrough videos (with metadata) explaining solutions to CS61A SP25 M |
| `youtube/[CS 61A SP25] Midterm 2 Walkthrough` | practice | 0.90 |  | Recorded Midterm 2 solution walkthrough videos (with metadata) organized by exam |
| `youtube/[CS 61A SU23] Midterm Walkthrough` | practice | 0.90 |  | Recorded walkthrough videos (with metadata) explaining solutions to CS61A Summer |
| `youtube/[CS 61A SU24 Discussion 2]` | study | 0.91 |  | Recorded discussion/lecture walkthrough videos (with metadata) for CS61A Discuss |
| `youtube/[CS 61A SU24] Discussion 01` | study | 0.93 |  | Recorded discussion walkthrough videos for CS61A SU24 Discussion 01 problems for |
| `youtube/[CS 61A SU24] Final Walkthrough` | practice | 0.74 |  | Video walkthroughs with solutions/explanations for each problem on the CS61A Sum |
| `youtube/[CS 61A SU24] Midterm Walkthrough` | practice | 0.85 |  | Videos that walk through solutions to each CS61A SU24 midterm problem for exam p |
| `youtube/[CS 61A SU25] Discussion 3` | study | 0.92 |  | Recorded Discussion 3 walkthrough videos (with metadata) teaching recursion prob |
| `youtube/[CS 61A SU25] Discussion 6` | study | 0.90 |  | Recorded Discussion 6 walkthrough videos (with metadata) teaching OOP concepts l |
| `youtube/[CS61A - Sp15] Final Solution Videos` | practice | 0.91 |  | Video walkthroughs providing solutions to CS61A final exam problems (with accomp |

## 3. Destination Tree

```
practice/  (1334 files)
  hw/  (66 files)
    hw01/  (2 files)
        hw01.zip
        hw01.zip_metadata.yaml
    hw02/  (4 files)
        hw02.py
        hw02.py_metadata.yaml
        hw02.zip
        hw02.zip_metadata.yaml
    hw03/  (4 files)
        hw03.py
        hw03.py_metadata.yaml
        hw03.zip
        hw03.zip_metadata.yaml
    hw04/  (4 files)
        hw04.py
        hw04.py_metadata.yaml
        hw04.zip
        hw04.zip_metadata.yaml
    hw05/  (6 files)
        hw05.py
        hw05.py_metadata.yaml
        hw05.scm
        hw05.scm_metadata.yaml
        hw05.zip
        hw05.zip_metadata.yaml
    hw06/  (4 files)
        hw06.sql
        hw06.sql_metadata.yaml
        hw06.zip
        hw06.zip_metadata.yaml
    sol-hw01/  (4 files)
        hw01.py
        hw01.py_metadata.yaml
        hw01.zip
        hw01.zip_metadata.yaml
    sol-hw02/  (4 files)
        hw02.py
        hw02.py_metadata.yaml
        hw02.zip
        hw02.zip_metadata.yaml
    sol-hw03/  (4 files)
        hw03.py
        hw03.py_metadata.yaml
        hw03.zip
        hw03.zip_metadata.yaml
    sol-hw04/  (4 files)
        hw04.py
        hw04.py_metadata.yaml
        hw04.zip
        hw04.zip_metadata.yaml
    sol-hw05/  (4 files)
        hw05.py
        hw05.py_metadata.yaml
        hw05.zip
        hw05.zip_metadata.yaml
      Homework 1 CS 61A Summer 2025.html
      Homework 1 CS 61A Summer 2025.html_metadata.yaml
      Homework 1 Solutions CS 61A Summer 2025.html
      Homework 1 Solutions CS 61A Summer 2025.html_metadata.yaml
      Homework 2 CS 61A Summer 2025.html
      ... and 17 more
  lab/  (92 files)
    lab00/  (2 files)
        lab00.zip
        lab00.zip_metadata.yaml
    lab01/  (2 files)
        lab01.zip
        lab01.zip_metadata.yaml
    lab02/  (2 files)
        lab02.zip
        lab02.zip_metadata.yaml
    lab03/  (2 files)
        lab03.zip
        lab03.zip_metadata.yaml
    lab04/  (2 files)
        lab04.zip
        lab04.zip_metadata.yaml
    lab05/  (2 files)
        lab05.zip
        lab05.zip_metadata.yaml
    lab06/  (2 files)
        lab06.zip
        lab06.zip_metadata.yaml
    lab07/  (2 files)
        lab07.zip
        lab07.zip_metadata.yaml
    lab08/  (2 files)
        lab08.zip
        lab08.zip_metadata.yaml
    lab09/  (2 files)
        lab09.zip
        lab09.zip_metadata.yaml
    lab10/  (2 files)
        lab10.zip
        lab10.zip_metadata.yaml
    lab11/  (2 files)
        lab11.zip
        lab11.zip_metadata.yaml
    lab12/  (2 files)
        lab12.zip
        lab12.zip_metadata.yaml
    sol-lab00/  (2 files)
        lab00.zip
        lab00.zip_metadata.yaml
    sol-lab01/  (2 files)
        lab01.zip
        lab01.zip_metadata.yaml
    sol-lab02/  (2 files)
        lab02.zip
        lab02.zip_metadata.yaml
    sol-lab03/  (2 files)
        lab03.zip
        lab03.zip_metadata.yaml
    sol-lab04/  (2 files)
        lab04.zip
        lab04.zip_metadata.yaml
    sol-lab05/  (2 files)
        lab05.zip
        lab05.zip_metadata.yaml
    sol-lab06/  (2 files)
        lab06.zip
        lab06.zip_metadata.yaml
    sol-lab07/  (2 files)
        lab07.zip
        lab07.zip_metadata.yaml
    sol-lab08/  (2 files)
        lab08.zip
        lab08.zip_metadata.yaml
    sol-lab09/  (2 files)
        lab09.zip
        lab09.zip_metadata.yaml
      Lab 0 Getting Started CS 61A Summer 2025.html
      Lab 0 Getting Started CS 61A Summer 2025.html_metadata.yaml
      Lab 0 Solutions CS 61A Summer 2025.html
      Lab 0 Solutions CS 61A Summer 2025.html_metadata.yaml
      Lab 1 Functions, Control CS 61A Summer 2025.html
      ... and 41 more
  proj/  (368 files)
    ants/  (5 files)
      diagram/  (3 files)
          ... 3 files
        ants.zip
        ants.zip_metadata.yaml
    cats/  (2 files)
        cats.zip
        cats.zip_metadata.yaml
    hog/  (2 files)
        hog.zip
        hog.zip_metadata.yaml
    scheme/  (351 files)
      scheme/  (349 files)
        abstract_turtle/  (10 files)
        editor/  (292 files)
        images/  (12 files)
        tests/  (20 files)
          ... 15 files
        scheme.zip
        scheme.zip_metadata.yaml
      Ants Vs. SomeBees CS 61A Summer 2025.html
      Ants Vs. SomeBees CS 61A Summer 2025.html_metadata.yaml
      Computer Aided Typing Software CS 61A Summer 2025.html
      Computer Aided Typing Software CS 61A Summer 2025.html_metadata.yaml
      Scheme Interpreter CS 61A Summer 2025.html
      Scheme Interpreter CS 61A Summer 2025.html_metadata.yaml
      The Game of Hog CS 61A Summer 2025.html
      The Game of Hog CS 61A Summer 2025.html_metadata.yaml
  youtube/  (808 files)
    CS 61A Fall 2016 Midterm 1/  (18 files)
        1-CS 61A Fall 2016 Midterm 1： Exeggcute - Question 1.mkv
        1-CS 61A Fall 2016 Midterm 1： Exeggcute - Question 1.mkv_metadata.yaml
        2-CS 61A Fall 2016 Midterm 1： Goldeen State - Question 2.mkv
        2-CS 61A Fall 2016 Midterm 1： Goldeen State - Question 2.mkv_metadata.yaml
        3-CS 61A Fall 2016 Midterm 1： Countizard - Question 3a.mkv
        ... and 13 more
    CS 61A Fall 2017 Final/  (16 files)
        1-CS 61A Fall 2017 Final – Question 1 (WWPD).mkv
        1-CS 61A Fall 2017 Final – Question 1 (WWPD).mkv_metadata.yaml
        2-CS 61A Fall 2017 Final – Question 2 (Environment Diagram).mkv
        2-CS 61A Fall 2017 Final – Question 2 (Environment Diagram).mkv_metadata.yaml
        3-CS 61A Fall 2017 Final – Question 3 (Box and Pointer).mkv
        ... and 11 more
    CS 61A Fall 2017 Midterm 1/  (14 files)
        1-CS 61A Fall 2017 Midterm 1： Question 1 - WWPD.mkv
        1-CS 61A Fall 2017 Midterm 1： Question 1 - WWPD.mkv_metadata.yaml
        2-CS 61A Fall 2017 Midterm 1： Question 2 - Environmental Influences.mkv
        2-CS 61A Fall 2017 Midterm 1： Question 2 - Environmental Influences.mkv_metadata.yaml
        3-CS 61A Fall 2017 Midterm 1： Question 3 - Triangulate.mkv
        ... and 9 more
    CS 61A Fall 2017 Midterm 2/  (18 files)
        1-CS 61A Fall 2017 Midterm 2： Question 1 - WWPD.mkv
        1-CS 61A Fall 2017 Midterm 2： Question 1 - WWPD.mkv_metadata.yaml
        2-CS 61A Fall 2017 Midterm 2： Question 2 - Buy Local.mkv
        2-CS 61A Fall 2017 Midterm 2： Question 2 - Buy Local.mkv_metadata.yaml
        3-CS 61A Fall 2017 Midterm 2： Question 3a & 3b - splice & all_splice.mkv
        ... and 13 more
    CS 61A Fall 2018 Final/  (26 files)
        1-CS61A Fall 2018 Final Q1.mkv
        1-CS61A Fall 2018 Final Q1.mkv_metadata.yaml
        10-CS61A Fall 2018 Final Q6.mkv
        10-CS61A Fall 2018 Final Q6.mkv_metadata.yaml
        11-CS61A Fall 2018 Final Q7a.mkv
        ... and 21 more
    CS 61A Fall 2018 Midterm 1/  (14 files)
        1-CS61A Fa18 MT1 Q1.mkv
        1-CS61A Fa18 MT1 Q1.mkv_metadata.yaml
        2-CS61A Fa18 MT1 Q2.mkv
        2-CS61A Fa18 MT1 Q2.mkv_metadata.yaml
        3-CS61A Fa18 MT1 Q3.mkv
        ... and 9 more
    CS 61A Fall 2018 Midterm 2/  (5 files)
        1-CS61A Fall 2018 MT2 Q1.mkv
        1-CS61A Fall 2018 MT2 Q1.mkv_metadata.yaml
        3-CS61A Fall 2018 MT2 Q3.mkv
        4-CS61A Fall 2018 MT2 Q4a.mkv
        5-CS61A Fall 2018 MT2 Q4.mkv
    CS 61A Fall 2020： Midterm 1 Walkthrough/  (8 files)
        1-[Fa20] 61A MT1 Q1 Walkthrough.mkv
        1-[Fa20] 61A MT1 Q1 Walkthrough.mkv_metadata.yaml
        2-[Fa20] 61A MT1 Q2 Walkthrough.mkv
        2-[Fa20] 61A MT1 Q2 Walkthrough.mkv_metadata.yaml
        3-[Fa20] 61A MT1 Q3 Walkthrough.mkv
        3-[Fa20] 61A MT1 Q3 Walkthrough.mkv_metadata.yaml
        4-[Fa20] 61A MT1 Q4 Walkthrough.mkv
        4-[Fa20] 61A MT1 Q4 Walkthrough.mkv_metadata.yaml
    CS 61A Fall 2020： Midterm 2 Walkthrough/  (8 files)
        1-[Fa20] 61A MT2 Q1 Walkthrough.mkv
        1-[Fa20] 61A MT2 Q1 Walkthrough.mkv_metadata.yaml
        2-[Fa20] 61A MT2 Q2 Walkthrough.mkv
        2-[Fa20] 61A MT2 Q2 Walkthrough.mkv_metadata.yaml
        3-[Fa20] 61A MT2 Q3 Walkthrough.webm
        3-[Fa20] 61A MT2 Q3 Walkthrough.webm_metadata.yaml
        4-[Fa20] 61A MT2 Q4 Walkthrough.mkv
        4-[Fa20] 61A MT2 Q4 Walkthrough.mkv_metadata.yaml
    CS 61A Fall 2021 Final Walkthrough/  (30 files)
        1-CS 61A Fall 2021 Final Q1A.mkv
        1-CS 61A Fall 2021 Final Q1A.mkv_metadata.yaml
        10-CS 61A Fall 2021 Final Q4A.mkv
        10-CS 61A Fall 2021 Final Q4A.mkv_metadata.yaml
        11-CS 61A Fall 2021 Final Q4B.mkv
        ... and 25 more
    CS 61A Spring 2016 Final/  (22 files)
        1-CS 61A Spring 2016 Final： Silence of the Lambdas - Question 1.mkv
        1-CS 61A Spring 2016 Final： Silence of the Lambdas - Question 1.mkv_metadata.yaml
        10-CS 61A Spring 2016 Final： Treebeard's Revenge - Question 10.mkv
        10-CS 61A Spring 2016 Final： Treebeard's Revenge - Question 10.mkv_metadata.yaml
        11-CS 61A Spring 2016 Final： Exstream! - Question 11.mkv
        ... and 17 more
    CS 61A Spring 2017 Final/  (28 files)
        1-CS 61A Spring 2017 Final： Q1 - WWPD.mkv
        1-CS 61A Spring 2017 Final： Q1 - WWPD.mkv_metadata.yaml
        10-CS 61A Spring 2017 Final： Q8 - Pair Up.mkv
        10-CS 61A Spring 2017 Final： Q8 - Pair Up.mkv_metadata.yaml
        11-CS 61A Spring 2017 Final： Q9a - Don't Go Down Part a.mkv
        ... and 23 more
    CS 61A Spring 2017 Midterm 2 Walkthrough/  (20 files)
        1-CS 61A Spring 2017 Midterm 2： Pointers - 1a.mkv
        1-CS 61A Spring 2017 Midterm 2： Pointers - 1a.mkv_metadata.yaml
        10-CS 61A Spring 2017 Midterm 2： Inflections - 5 (Generator Version).mkv
        11-CS 61A Spring 2017 Midterm 2： Inflections - 5 (Iterator Version).mkv
        12-CS 61A Spring 2017 Midterm 2： Tree Paths - 6.mkv
        ... and 15 more
    CS 61A Spring 2018 Final Walkthrough/  (26 files)
        1-CS 61A Spring 2018 Final Walkthrough： Q1.mp4
        1-CS 61A Spring 2018 Final Walkthrough： Q1.mp4_metadata.yaml
        10-CS 61A Spring 2018 Final Walkthrough： Q6c.mp4
        10-CS 61A Spring 2018 Final Walkthrough： Q6c.mp4_metadata.yaml
        11-CS 61A Spring 2018 Final Walkthrough： Q6d.mp4
        ... and 21 more
    CS 61A Spring 2018 Midterm 1/  (8 files)
        1-CS 61A Spring 2018 Midterm 1 – Problem 1.mkv
        1-CS 61A Spring 2018 Midterm 1 – Problem 1.mkv_metadata.yaml
        2-CS 61A Spring 2018 Midterm 1 – Problem 2.mkv
        2-CS 61A Spring 2018 Midterm 1 – Problem 2.mkv_metadata.yaml
        3-CS 61A Spring 2018 Midterm 1 – Problem 3.mkv
        3-CS 61A Spring 2018 Midterm 1 – Problem 3.mkv_metadata.yaml
        4-CS 61A Spring 2018 Midterm 1 – Problem 4.mkv
        4-CS 61A Spring 2018 Midterm 1 – Problem 4.mkv_metadata.yaml
    CS 61A Spring 2018 Midterm 2/  (10 files)
        1-CS 61A Spring 2018 Midterm 2 – Problem 1 (WWPD).mkv
        1-CS 61A Spring 2018 Midterm 2 – Problem 1 (WWPD).mkv_metadata.yaml
        2-CS 61A Spring 2018 Midterm 2 – Problem 2 (Environment Diagrams).mkv
        2-CS 61A Spring 2018 Midterm 2 – Problem 2 (Environment Diagrams).mkv_metadata.yaml
        3-CS 61A Spring 2018 Midterm 2 – Problem 3 (Lists).mkv
        3-CS 61A Spring 2018 Midterm 2 – Problem 3 (Lists).mkv_metadata.yaml
        4-CS 61A Spring 2018 Midterm 2 – Problem 4 (Sequences).mkv
        4-CS 61A Spring 2018 Midterm 2 – Problem 4 (Sequences).mkv_metadata.yaml
        5-CS 61A Spring 2018 Midterm 2 – Problem 5 (Trees).mkv
        5-CS 61A Spring 2018 Midterm 2 – Problem 5 (Trees).mkv_metadata.yaml
    CS 61A Spring 2019 Midterm 1/  (6 files)
        1-Spring 2019 Midterm 1 Q1.mkv
        1-Spring 2019 Midterm 1 Q1.mkv_metadata.yaml
        2-Spring 2019 Midterm 1 Q2.mkv
        2-Spring 2019 Midterm 1 Q2.mkv_metadata.yaml
        3-Spring 2019 Midterm 1 Q3.mkv
        3-Spring 2019 Midterm 1 Q3.mkv_metadata.yaml
    CS 61A Spring 2019 Midterm 2/  (12 files)
        1-CS61A Sp19 MT2 Q1.mkv
        1-CS61A Sp19 MT2 Q1.mkv_metadata.yaml
        2-CS61A Sp19 MT2 Q2.mkv
        2-CS61A Sp19 MT2 Q2.mkv_metadata.yaml
        3-CS61A Sp19 MT2 Q3.mkv
        ... and 7 more
    CS 61A Spring 2022 Final Walkthrough/  (30 files)
        1-CS 61A Spring 2022 Final Q1.webm
        1-CS 61A Spring 2022 Final Q1.webm_metadata.yaml
        10-CS 61A Spring 2022 Final Q10.webm
        10-CS 61A Spring 2022 Final Q10.webm_metadata.yaml
        11-CS 61A Spring 2022 Final Q11.webm
        ... and 25 more
    CS 61A Summer 2017 Final Walkthrough/  (14 files)
        1-CS 61A Summer 2017 Final： Q1 WWPD.webm
        1-CS 61A Summer 2017 Final： Q1 WWPD.webm_metadata.yaml
        2-CS 61A Summer 2017 Final： Q3 Environment Diagram.mkv
        2-CS 61A Summer 2017 Final： Q3 Environment Diagram.mkv_metadata.yaml
        3-CS 61A Summer 2017 Final： Q5 Trees.mkv
        ... and 9 more
    CS 61A Summer 2018 Final/  (6 files)
        1-CS 61A Summer 2018 Final： Q5b.mkv
        1-CS 61A Summer 2018 Final： Q5b.mkv_metadata.yaml
        2-CS 61A Summer 2018 Final： Q6.mkv
        2-CS 61A Summer 2018 Final： Q6.mkv_metadata.yaml
        3-CS 61A Summer 2018 Final： Q8.mkv
        3-CS 61A Summer 2018 Final： Q8.mkv_metadata.yaml
    CS 61A Summer 2018 Mock Final Walkthrough/  (20 files)
        1-CS 61A Summer 2018 Mock Final： Q1.mkv
        1-CS 61A Summer 2018 Mock Final： Q1.mkv_metadata.yaml
        10-CS 61A Summer 2018 Mock Final： Q6c.mkv
        10-CS 61A Summer 2018 Mock Final： Q6c.mkv_metadata.yaml
        2-CS 61A Summer 2018 Mock Final： Q2.mkv
        ... and 15 more
    CS 61A Summer 2022 Final Walkthrough/  (20 files)
        1-CS 61A Summer 2022 Final Q1.mkv
        1-CS 61A Summer 2022 Final Q1.mkv_metadata.yaml
        10-CS 61A Summer 2022 Final Q9.mkv
        10-CS 61A Summer 2022 Final Q9.mkv_metadata.yaml
        2-CS 61A Summer 2022 Final Q2.mkv
        ... and 15 more
    CS walkthrough vids/  (32 files)
        1-CS61a Fall 2016 Final WWPD.mkv
        1-CS61a Fall 2016 Final WWPD.mkv_metadata.yaml
        10-CS61a Fall 2016 Final reset B.mkv
        10-CS61a Fall 2016 Final reset B.mkv_metadata.yaml
        11-CS61a Fall 2016 Final I Scheme for Ice Cream A.mkv
        ... and 27 more
    CS61A Fall 2019 Final/  (32 files)
        1-CS61A Fall 2019 Final Q1.mkv
        1-CS61A Fall 2019 Final Q1.mkv_metadata.yaml
        10-CS61A Fall 2019 Final Q6e.webm
        10-CS61A Fall 2019 Final Q6e.webm_metadata.yaml
        11-CS61A Fall 2019 Q7a.webm
        ... and 27 more
    CS61A Fall 2019 Midterm 1/  (14 files)
        1-CS61A Fall 2019 Midterm 1 Q1.mkv
        1-CS61A Fall 2019 Midterm 1 Q1.mkv_metadata.yaml
        2-CS61A Fall 2019 Midterm 1 Q2.mkv
        2-CS61A Fall 2019 Midterm 1 Q2.mkv_metadata.yaml
        3-CS61A Fall 2019 Midterm 1 Q3.mkv
        ... and 9 more
    CS61A Fall 2019 Midterm 2/  (16 files)
        1-CS61A Fall 2019 Midterm 2 Q1.mkv
        1-CS61A Fall 2019 Midterm 2 Q1.mkv_metadata.yaml
        2-CS61A Fall 2019 Midterm 2 Q2.mkv
        2-CS61A Fall 2019 Midterm 2 Q2.mkv_metadata.yaml
        3-CS61A Fall 2019 Midterm 2 Q3.mkv
        ... and 11 more
    CS61A Fall 2021 Midterm 1 Walkthrough/  (14 files)
        1-61A FA21 MT1： Q1a.webm
        1-61A FA21 MT1： Q1a.webm_metadata.yaml
        2-61A FA21 MT1： Q1b.webm
        2-61A FA21 MT1： Q1b.webm_metadata.yaml
        3-61A FA21 MT1： Q2.webm
        ... and 9 more
    CS61A Fall 2021 Midterm 2/  (22 files)
        1-61A FA21 MT2 Q1： Hawkeye.mkv
        1-61A FA21 MT2 Q1： Hawkeye.mkv_metadata.yaml
        10-61A FA21 MT2 Q5a： Groot (Twig).webm
        10-61A FA21 MT2 Q5a： Groot (Twig).webm_metadata.yaml
        11-61A FA21 MT2 Q5b： Groot (Twigs).webm
        ... and 17 more
    CS61A Spring 2019 Final/  (20 files)
        1-CS61A Spring 2019 Final Q1.mkv
        1-CS61A Spring 2019 Final Q1.mkv_metadata.yaml
        10-CS61A Spring 2019 Final Q10.mkv
        10-CS61A Spring 2019 Final Q10.mkv_metadata.yaml
        2-CS61A Spring 2019 Final Q2.mkv
        ... and 15 more
    CS61A Spring 2020 Midterm 1/  (12 files)
        1-CS61A Spring 2020 Midterm 1 Q1.mkv
        1-CS61A Spring 2020 Midterm 1 Q1.mkv_metadata.yaml
        2-CS61A Spring 2020 Midterm 1 Q2.mkv
        2-CS61A Spring 2020 Midterm 1 Q2.mkv_metadata.yaml
        3-CS61A Spring 2020 Midterm 1 Q3ab.mkv
        ... and 7 more
    CS61A Spring 2022 Midterm 2 Walkthrough/  (32 files)
        1-61A SP22 MT2： Q1a.mkv
        1-61A SP22 MT2： Q1a.mkv_metadata.yaml
        10-61A SP22 MT2： Q8a.mkv
        10-61A SP22 MT2： Q8a.mkv_metadata.yaml
        11-61A SP22 MT2： Q8b (Readthrough).mkv
        ... and 27 more
    [CS 61A FA22] Midterm 1 Walkthrough/  (10 files)
        1-Midterm 1 Question 1 Walkthrough.webm
        1-Midterm 1 Question 1 Walkthrough.webm_metadata.yaml
        2-Midterm 1 Question 2 Walkthrough.webm
        2-Midterm 1 Question 2 Walkthrough.webm_metadata.yaml
        3-Midterm 1 Question 3 Walkthrough.mkv
        3-Midterm 1 Question 3 Walkthrough.mkv_metadata.yaml
        4-Midterm 1 Question 4 Walkthrough.mkv
        4-Midterm 1 Question 4 Walkthrough.mkv_metadata.yaml
        5-Midterm 1 Question 5 Walkthrough.mkv
        5-Midterm 1 Question 5 Walkthrough.mkv_metadata.yaml
    [CS 61A FA22] Midterm 2 Walkthrough Videos/  (14 files)
        1-Question 1： What would Python Python？.mkv
        1-Question 1： What would Python Python？.mkv_metadata.yaml
        2-Question 2： Environmental Disaster.mkv
        2-Question 2： Environmental Disaster.mkv_metadata.yaml
        3-Question 3： Hog Revisited.mkv
        ... and 9 more
    [CS 61A FA23] Final Walkthrough/  (16 files)
        1-[CS 61A FA23] Final Problem #1： Copying Copies.webm
        1-[CS 61A FA23] Final Problem #1： Copying Copies.webm_metadata.yaml
        2-[CS 61A FA23] Final Problem #2： Path Math.webm
        2-[CS 61A FA23] Final Problem #2： Path Math.webm_metadata.yaml
        3-[CS 61A FA23] Final Problem #3： Talk Like a Pirate Day.webm
        ... and 11 more
    [CS 61A FA23] Midterm 1 Walkthrough/  (8 files)
        1-Problem #1： What Would Python Display？.mkv
        1-Problem #1： What Would Python Display？.mkv_metadata.yaml
        2-Problem #2： An Odd Implementation of Even.mkv
        2-Problem #2： An Odd Implementation of Even.mkv_metadata.yaml
        3-Problem #3： In Your Prime.mkv
        3-Problem #3： In Your Prime.mkv_metadata.yaml
        4-Problem #4： Choose Wisely.mkv
        4-Problem #4： Choose Wisely.mkv_metadata.yaml
    [CS 61A FA23] Midterm 2 Walkthrough Videos/  (12 files)
        1-Question #1： What Would Python Display？.mkv
        1-Question #1： What Would Python Display？.mkv_metadata.yaml
        2-Question #2： Making a List, Checking it Twice.mkv
        2-Question #2： Making a List, Checking it Twice.mkv_metadata.yaml
        3-Question #3： 24-Hour Library.mkv
        ... and 7 more
    [CS 61A FA24] Midterm 1 Walkthrough/  (10 files)
        1-[CS 61A FA24] Midterm 1 Problem #1： WWPD？.webm
        1-[CS 61A FA24] Midterm 1 Problem #1： WWPD？.webm_metadata.yaml
        2-[CS 61A FA24] Midterm 1 Problem #2： Which One.webm
        2-[CS 61A FA24] Midterm 1 Problem #2： Which One.webm_metadata.yaml
        3-[CS 61A FA24] Midterm 1 Problem #3： Final Digit.webm
        3-[CS 61A FA24] Midterm 1 Problem #3： Final Digit.webm_metadata.yaml
        4-[CS 61A FA24] Midterm 1 Problem #4： Close Enough.webm
        4-[CS 61A FA24] Midterm 1 Problem #4： Close Enough.webm_metadata.yaml
        5-[CS 61A FA24] Midterm 1 Problem #5： Shifty.webm
        5-[CS 61A FA24] Midterm 1 Problem #5： Shifty.webm_metadata.yaml
    [CS 61A FA24] Midterm 2 Walkthrough/  (14 files)
        1-[CS 61A FA24] Midterm 2 Problem #1： What Would Python Display？.webm
        1-[CS 61A FA24] Midterm 2 Problem #1： What Would Python Display？.webm_metadata.yaml
        2-[CS 61A FA24] Midterm 2 Problem #2： Pizza by the Slice.mkv
        2-[CS 61A FA24] Midterm 2 Problem #2： Pizza by the Slice.mkv_metadata.yaml
        3-[CS 61A FA24] Midterm 2 Problem #3： CS 61A Software Store.mkv
        ... and 9 more
    [CS 61A SP23] Final Walkthrough/  (14 files)
        1-[CS 61A SP23] Final Problem #1： What Would Python Display？.webm
        1-[CS 61A SP23] Final Problem #1： What Would Python Display？.webm_metadata.yaml
        2-[CS 61A SP23] Final Problem #2： Framed.webm
        2-[CS 61A SP23] Final Problem #2： Framed.webm_metadata.yaml
        3-[CS 61A SP23] Final Problem #3： Trees Get Degrees.webm
        ... and 9 more
    [CS 61A SP23] Midterm 1 Walkthrough/  (10 files)
        1-Problem #1： What would Python Display？.mkv
        1-Problem #1： What would Python Display？.mkv_metadata.yaml
        2-Problem #2： Square the Square.mkv
        2-Problem #2： Square the Square.mkv_metadata.yaml
        3-Problem #3a： On Repeat.mkv
        3-Problem #3a： On Repeat.mkv_metadata.yaml
        4-Problem #3b： On Repeat.mkv
        4-Problem #3b： On Repeat.mkv_metadata.yaml
        5-Problem #4： Perfect Ten.mkv
        5-Problem #4： Perfect Ten.mkv_metadata.yaml
    [CS 61A SP23] Midterm 2 Walkthrough/  (20 files)
        1-Problem #1： What Would Python Display？.mkv
        1-Problem #1： What Would Python Display？.mkv_metadata.yaml
        10-Problem #5c： Parking.mkv
        10-Problem #5c： Parking.mkv_metadata.yaml
        2-Problem #2a： Letter Grade.mkv
        ... and 15 more
    [CS 61A SP24] Midterm 1 Walkthrough/  (8 files)
        1-[CS 61A SP24] Midterm 1 WWPD.mkv
        1-[CS 61A SP24] Midterm 1 WWPD.mkv_metadata.yaml
        2-[CS 61A SP24] Midterm 1 Silence of the Lambda.mkv
        2-[CS 61A SP24] Midterm 1 Silence of the Lambda.mkv_metadata.yaml
        3-[CS 61A SP24] Midterm 1 Nearly Square.mkv
        3-[CS 61A SP24] Midterm 1 Nearly Square.mkv_metadata.yaml
        4-[CS 61A SP24] Midterm 1 Nice Dice.mkv
        4-[CS 61A SP24] Midterm 1 Nice Dice.mkv_metadata.yaml
    [CS 61A SP24] Midterm 2 Walkthrough/  (8 files)
        1-[CS 61A SP24] Midterm 2 Problem #1 What Would Python Display Walkthrough.mkv
        1-[CS 61A SP24] Midterm 2 Problem #1 What Would Python Display Walkthrough.mkv_metadata.yaml
        2-[CS 61A SP24] Midterm 2 Problem #2 Spin Cycle.webm
        2-[CS 61A SP24] Midterm 2 Problem #2 Spin Cycle.webm_metadata.yaml
        3-[CS 61A SP24] Midterm 2 Problem #3 Fearless Walkthrough.mkv
        3-[CS 61A SP24] Midterm 2 Problem #3 Fearless Walkthrough.mkv_metadata.yaml
        4-[CS 61A SP24] Midterm 2 Problem #4 Who's counting？.mkv
        4-[CS 61A SP24] Midterm 2 Problem #4 Who's counting？.mkv_metadata.yaml
    [CS 61A SP25] Midterm 1 Walkthrough/  (10 files)
        1-[CS61A SP25] Midterm 1 Problem #1： What Would Python Print？.webm
        1-[CS61A SP25] Midterm 1 Problem #1： What Would Python Print？.webm_metadata.yaml
        2-[CS61A SP25] Midterm 1 Problem #2： Which One.webm
        2-[CS61A SP25] Midterm 1 Problem #2： Which One.webm_metadata.yaml
        3-[CS61A SP25] Midterm 1 Problem #3： Legit Digit.webm
        3-[CS61A SP25] Midterm 1 Problem #3： Legit Digit.webm_metadata.yaml
        4-[CS61A SP25] Midterm 1 Problem #4： Hailstone Returns.mkv
        4-[CS61A SP25] Midterm 1 Problem #4： Hailstone Returns.mkv_metadata.yaml
        5-[CS61A SP25] Midterm 1 Problem #5： It Takes Two.mkv
        5-[CS61A SP25] Midterm 1 Problem #5： It Takes Two.mkv_metadata.yaml
    [CS 61A SP25] Midterm 2 Walkthrough/  (16 files)
        1-[CS 61A SP25] Midterm 2 Problem #1： What Would Python Do？.mkv
        1-[CS 61A SP25] Midterm 2 Problem #1： What Would Python Do？.mkv_metadata.yaml
        2-[CS 61A SP25] Midterm 2 Problem #3a： Berkeley Time.mkv
        2-[CS 61A SP25] Midterm 2 Problem #3a： Berkeley Time.mkv_metadata.yaml
        3-[CS 61A SP25] Midterm 2 Problem #3b： Berkeley Time.mkv
        ... and 11 more
    [CS 61A SU23] Midterm Walkthrough/  (9 files)
        1-[CS 61A SU23] Midterm Problem #1： WWPD？.webm
        1-[CS 61A SU23] Midterm Problem #1： WWPD？.webm_metadata.yaml
        2-[CS 61A SU23] Midterm Problem #2： The Fellowship of the List.mkv
        2-[CS 61A SU23] Midterm Problem #2： The Fellowship of the List.mkv_metadata.yaml
        4-[CS 61A SU23] Midterm Problem #4： Goatda.webm
        5-[CS 61A SU23] Midterm Problem #5a： Sweetness Overload.mkv
        6-[CS 61A SU23] Midterm Problem #5b： Sweetness Overload.mkv
        7-[CS 61A SU23] Midterm Problem #5c： Sweetness Overload.mkv
        8-[CS 61A SU23] Midterm Problem #6： All Treeils Lead to Rome.webm
    [CS 61A SU24] Midterm Walkthrough/  (14 files)
        1-[CS 61A SU24] Midterm Problem #1： Generiterator.mkv
        1-[CS 61A SU24] Midterm Problem #1： Generiterator.mkv_metadata.yaml
        2-[CS 61A SU24] Midterm Problem #2： Conveyor Belt.mkv
        2-[CS 61A SU24] Midterm Problem #2： Conveyor Belt.mkv_metadata.yaml
        3-[CS 61A SU24] Midterm Problem #3： Tree Sum.webm
        ... and 9 more
    [CS61A - Sp15] Final Solution Videos/  (42 files)
        1-[CS61A - Sp15] Final Fall 2014 - Problem 6b.mkv
        1-[CS61A - Sp15] Final Fall 2014 - Problem 6b.mkv.json
        1-[CS61A - Sp15] Final Fall 2014 - Problem 6b.mkv_metadata.yaml
        10-[CS61A - Sp15] Final Fall 2014 - Problem 4a.mkv
        10-[CS61A - Sp15] Final Fall 2014 - Problem 4a.mkv.json
        ... and 37 more
study/  (826 files)
  lecture/  (826 files)
    assets/  (136 files)
      pdfs/  (6 files)
          ... 6 files
      slides/  (130 files)
          ... 130 files
    disc/  (98 files)
      disc00/  (3 files)
          ... 3 files
      disc01/  (3 files)
          ... 3 files
      disc02/  (3 files)
          ... 3 files
      disc03/  (3 files)
          ... 3 files
      disc04/  (3 files)
          ... 3 files
      disc05/  (3 files)
          ... 3 files
      disc06/  (3 files)
          ... 3 files
      disc07/  (3 files)
          ... 3 files
      disc08/  (3 files)
          ... 3 files
      disc09/  (3 files)
          ... 3 files
      disc10/  (3 files)
          ... 3 files
      disc11/  (3 files)
          ... 3 files
      disc12/  (3 files)
          ... 3 files
      sol-disc00/  (3 files)
          ... 3 files
      sol-disc01/  (3 files)
          ... 3 files
      sol-disc02/  (3 files)
          ... 3 files
      sol-disc03/  (3 files)
          ... 3 files
      sol-disc04/  (3 files)
          ... 3 files
      sol-disc05/  (3 files)
          ... 3 files
      sol-disc06/  (3 files)
          ... 3 files
      sol-disc07/  (3 files)
          ... 3 files
      sol-disc08/  (3 files)
          ... 3 files
      sol-disc09/  (3 files)
          ... 3 files
      sol-disc10/  (3 files)
          ... 3 files
        Discussion 0 CS 61A Summer 2025.html
        Discussion 0 CS 61A Summer 2025.html_metadata.yaml
        Discussion 1 CS 61A Summer 2025.html
        Discussion 1 CS 61A Summer 2025.html_metadata.yaml
        Discussion 10 CS 61A Summer 2025.html
        ... and 21 more
    study-guide/  (2 files)
        Study Guide Orders of Growth CS 61A Summer 2025.html
        Study Guide Orders of Growth CS 61A Summer 2025.html_metadata.yaml
    youtube/  (590 files)
      61A Fall 2023 Lecture 32/  (15 files)
          ... 15 files
      Aggregation and Databases (Su25)/  (15 files)
          ... 15 files
      CS 61A Fall 2016 Midterm 2/  (2 files)
          ... 2 files
      Control/  (12 files)
          ... 12 files
      Data Abstraction and ADT Trees (Su25)/  (27 files)
          ... 27 files
      Disc 07/  (15 files)
          ... 15 files
      Discussion 10： Tail Calls, Scheme Data Abstractions, SQL/  (9 files)
          ... 9 files
      Discussion 4： Tree Recursion, Trees, Lists/  (12 files)
          ... 12 files
      Discussion 5： Iterators, Generators, Efficiency/  (17 files)
          ... 17 files
      Discussion 9： Interpreters/  (9 files)
          ... 9 files
      Efficiency/  (18 files)
          ... 18 files
      Environments/  (18 files)
          ... 18 files
      Environments (Su25)/  (24 files)
          ... 24 files
      Final Review (Su25)/  (9 files)
          ... 9 files
      Functions/  (18 files)
          ... 18 files
      Higher-Order Functions/  (15 files)
          ... 15 files
      Inheritance and Representation (Su25)/  (27 files)
          ... 27 files
      Interpreters (Su25)/  (36 files)
          ... 36 files
      Iterators and Generators (Su25)/  (30 files)
          ... 30 files
      Linked Lists (Su25)/  (15 files)
          ... 15 files
      Midterm Review (Su25)/  (6 files)
          ... 6 files
      Mutability (Su25)/  (21 files)
          ... 21 files
      Mutable Trees (Su25)/  (6 files)
          ... 6 files
      Objects and Attributes (Su25)/  (27 files)
          ... 27 files
      Recursion/  (18 files)
          ... 18 files
      SQL and Tables (Su25)/  (24 files)
          ... 24 files
      Scheme/  (18 files)
          ... 18 files
      Scheme Lists/  (15 files)
          ... 15 files
      Sequences and Containers (Su25)/  (33 files)
          ... 33 files
      Tail Calls (Su25)/  (12 files)
          ... 12 files
      Tree Recursion/  (12 files)
          ... 12 files
      [CS 61A SP24] Mega Discussion 08/  (10 files)
          ... 10 files
      [CS 61A SU24 Discussion 2]/  (10 files)
          ... 10 files
      [CS 61A SU24] Discussion 01/  (10 files)
          ... 10 files
      [CS 61A SU24] Final Walkthrough/  (8 files)
          ... 8 files
      [CS 61A SU25] Discussion 3/  (8 files)
          ... 8 files
      [CS 61A SU25] Discussion 6/  (6 files)
          ... 6 files
        CS 61A Fall 2015 Final Walkthrough.mkv
        Interleave Digits.mkv
        Interleave Digits.mkv_metadata.yaml
support/  (470 files)
  articles/  (26 files)
      Advice from Former Students CS 61A Summer 2025.html
      Advice from Former Students CS 61A Summer 2025.html_metadata.yaml
      CS 61A Scheme Specification CS 61A Summer 2025.html
      CS 61A Scheme Specification CS 61A Summer 2025.html_metadata.yaml
      Composition CS 61A Summer 2025.html
      ... and 21 more
  assignment-calendar/  (2 files)
      Office Hours CS 61A Summer 2025.html
      Office Hours CS 61A Summer 2025.html_metadata.yaml
  exam/  (324 files)
    fa16/  (12 files)
      mt1/  (6 files)
          ... 6 files
      mt2/  (6 files)
          ... 6 files
    fa17/  (14 files)
      final/  (2 files)
          ... 2 files
      mt1/  (6 files)
          ... 6 files
      mt2/  (6 files)
          ... 6 files
    fa18/  (16 files)
      final/  (4 files)
          ... 4 files
      mt1/  (6 files)
          ... 6 files
      mt2/  (6 files)
          ... 6 files
    fa19/  (18 files)
      final/  (6 files)
          ... 6 files
      mt1/  (6 files)
          ... 6 files
      mt2/  (6 files)
          ... 6 files
    fa20/  (12 files)
      final/  (4 files)
          ... 4 files
      mt1/  (4 files)
          ... 4 files
      mt2/  (4 files)
          ... 4 files
    fa21/  (12 files)
      final/  (4 files)
          ... 4 files
      mt1/  (4 files)
          ... 4 files
      mt2/  (4 files)
          ... 4 files
    fa22/  (12 files)
      final/  (4 files)
          ... 4 files
      mt1/  (4 files)
          ... 4 files
      mt2/  (4 files)
          ... 4 files
    fa23/  (12 files)
      final/  (4 files)
          ... 4 files
      mt1/  (4 files)
          ... 4 files
      mt2/  (4 files)
          ... 4 files
    fa24/  (12 files)
      final/  (4 files)
          ... 4 files
      mt1/  (4 files)
          ... 4 files
      mt2/  (4 files)
          ... 4 files
    sp16/  (6 files)
      mt1/  (6 files)
          ... 6 files
    sp17/  (12 files)
      mt1/  (6 files)
          ... 6 files
      mt2/  (6 files)
          ... 6 files
    sp18/  (14 files)
      final/  (2 files)
          ... 2 files
      mt1/  (6 files)
          ... 6 files
      mt2/  (6 files)
          ... 6 files
    sp19/  (14 files)
      final/  (4 files)
          ... 4 files
      mt1/  (6 files)
          ... 6 files
      mt2/  (4 files)
          ... 4 files
    sp20/  (14 files)
      final/  (4 files)
          ... 4 files
      mt1/  (6 files)
          ... 6 files
      mt2/  (4 files)
          ... 4 files
    sp21/  (24 files)
      final/  (4 files)
          ... 4 files
      mt1/  (6 files)
          ... 6 files
      mt2/  (6 files)
          ... 6 files
      practice-final/  (4 files)
          ... 4 files
      practice-mt1/  (4 files)
          ... 4 files
    sp22/  (16 files)
      final/  (4 files)
          ... 4 files
      mt1/  (6 files)
          ... 6 files
      mt2/  (6 files)
          ... 6 files
    sp23/  (12 files)
      final/  (4 files)
          ... 4 files
      mt1/  (4 files)
          ... 4 files
      mt2/  (4 files)
          ... 4 files
    sp24/  (12 files)
      final/  (4 files)
          ... 4 files
      mt1/  (4 files)
          ... 4 files
      mt2/  (4 files)
          ... 4 files
    sp25/  (12 files)
      final/  (4 files)
          ... 4 files
      mt1/  (4 files)
          ... 4 files
      mt2/  (4 files)
          ... 4 files
    su19/  (8 files)
      final/  (4 files)
          ... 4 files
      mt/  (4 files)
          ... 4 files
    su20/  (16 files)
      final/  (4 files)
          ... 4 files
      mt1/  (4 files)
          ... 4 files
      mt2/  (4 files)
          ... 4 files
      practice-mt/  (4 files)
          ... 4 files
    su21/  (16 files)
      diagnostic/  (4 files)
          ... 4 files
      final/  (4 files)
          ... 4 files
      midterm/  (4 files)
          ... 4 files
      practice-diagnostic/  (4 files)
          ... 4 files
    su22/  (8 files)
      final/  (4 files)
          ... 4 files
      midterm/  (4 files)
          ... 4 files
    su23/  (8 files)
      final/  (4 files)
          ... 4 files
      midterm/  (4 files)
          ... 4 files
    su24/  (8 files)
      final/  (4 files)
          ... 4 files
      midterm/  (4 files)
          ... 4 files
    su25/  (4 files)
      midterm/  (4 files)
          ... 4 files
  instructor/  (2 files)
      Instructors CS 61A Summer 2025.html
      Instructors CS 61A Summer 2025.html_metadata.yaml
  lecture/  (2 files)
      CS 61A Summer 2025.html
      CS 61A Summer 2025.html_metadata.yaml
  office-hours/  (2 files)
      Office Hours CS 61A Summer 2025.html
      Office Hours CS 61A Summer 2025.html_metadata.yaml
  resources/  (2 files)
      CS 61A Summer 2025.html
      CS 61A Summer 2025.html_metadata.yaml
  staff/  (2 files)
      Course Staff CS 61A Summer 2025.html
      Course Staff CS 61A Summer 2025.html_metadata.yaml
  textbook/  (94 files)
    about.html/  (2 files)
        About Composing Programs.html
        About Composing Programs.html_metadata.yaml
    examples/  (34 files)
      mapreduce/  (2 files)
          ... 2 files
      parallel/  (8 files)
          ... 8 files
      scalc/  (24 files)
          ... 24 files
    pages/  (56 files)
        1.1 Getting Started.html
        1.1 Getting Started.html_metadata.yaml
        1.2 Elements of Programming.html
        1.2 Elements of Programming.html_metadata.yaml
        1.3 Defining New Functions.html
        ... and 51 more
    projects.html/  (2 files)
        Programming Projects.html
        Programming Projects.html_metadata.yaml
  youtube/  (14 files)
    Discussion 5： Iterators, Generators, Efficiency/  (1 files)
        1-[CS 61A FA23] Final Problem #1： Copying Copies.webm_metadata.yaml
    [CS 61A SU24] Final Walkthrough/  (7 files)
        1-[CS61A SU24] Final Problem #1： Phrase Phonetics.webm_metadata.yaml
        2-[CS61A SU24] Final Problem #2： Sweet Diadreams.webm_metadata.yaml
        3-[CS61A SU24] Final Problem #3： Movie Theater Seating.webm_metadata.yaml
        4-[CS61A SU24] Final Problem #4： Linked Max Composite Value Path.webm_metadata.yaml
        5-[CS61A SU24] Final Problem #5： CS61A Web Browser.webm_metadata.yaml
        7-[CS61A SU24] Final Problem #7： Scheme Dictionary Abstraction.webm_metadata.yaml
        8-[CS61A SU24] Final Problem #8： Phrase Pho.webm_metadata.yaml
      CS 61A Fall 2015 Final Walkthrough.mkv_metadata.yaml
      CS 61A Spring 2016 Midterm 1 Walkthrough.mkv
      CS 61A Spring 2016 Midterm 1 Walkthrough.mkv_metadata.yaml
      CS 61A Spring 2016 Midterm 2 Walkthrough.mkv
      CS 61A Spring 2016 Midterm 2 Walkthrough.mkv_metadata.yaml
      Interleave Digits.mkv.json
```

## 5. Files Not Found in DB

2134 files on disk have no matching DB entry 
(classified without descriptions):

- `01-Welcome_1pp.pdf_content_list.json`
- `01-Welcome_1pp.pdf_metadata.yaml`
- `01.py_metadata.yaml`
- `02-Functions_1pp.pdf_content_list.json`
- `02-Functions_1pp.pdf_metadata.yaml`
- `02.py_metadata.yaml`
- `03-Control_1pp.pdf_content_list.json`
- `03-Control_1pp.pdf_metadata.yaml`
- `03.py_metadata.yaml`
- `04-Higher-Order_Functions_1pp.pdf_content_list.json`
- `04-Higher-Order_Functions_1pp.pdf_metadata.yaml`
- `04.py_metadata.yaml`
- `05-Environments_1pp.pdf_content_list.json`
- `05-Environments_1pp.pdf_metadata.yaml`
- `05.py_metadata.yaml`
- `06-Recursion_1pp.pdf_content_list.json`
- `06-Recursion_1pp.pdf_metadata.yaml`
- `06.py_metadata.yaml`
- `07-Tree_Recursion_1pp.pdf_content_list.json`
- `07-Tree_Recursion_1pp.pdf_metadata.yaml`
- `07.py_metadata.yaml`
- `08-Sequences_and_Containers_1pp.pdf_content_list.json`
- `08-Sequences_and_Containers_1pp.pdf_metadata.yaml`
- `08.py_metadata.yaml`
- `09-Data_Abstraction_and_ADT_Trees_1pp.pdf_content_list.json`
- `09-Data_Abstraction_and_ADT_Trees_1pp.pdf_metadata.yaml`
- `09.py_metadata.yaml`
- `1-61A FA21 MT1： Q1a.webm`
- `1-61A FA21 MT1： Q1a.webm_metadata.yaml`
- `1-61A FA21 MT2 Q1： Hawkeye.mkv`
- ... and 2104 more
