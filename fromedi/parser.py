from fromedi.defs import Token, SegmentType, Defs
import logging

logging.basicConfig(format='%(asctime)s %(funcName)s: %(message)s', level=logging.DEBUG)


class Parser:
    def __init__(self):
        self._out = {}  # Final result after parsing

        # Keep track of the outer rules when we process child elements that
        # contains nested rules. We will need to comeback to previous rule later on
        # to finialize the info
        #
        # Example:
        # From rule ISA, we switch to GS then switch to ST, and process some regular segments
        # such as BIG and REF <-- These do not contain nested segment so we do not need to track them
        # --> Stack [ISA, GS, ST]
        # Then we switch to N1-Loop rule which contains nested segments N1, N2, N3, N4
        # --> Stack [ISA, GS, ST, N1Loop].
        # After we've done processing with N1Loop, we remove it from stacked
        # --> [ISA, GS, ST] and continue with remaining elements of ST rule)

        self.rule_stack = [{'subsegs': Defs.rule}]

        # Stack contains _out's parent keys
        #
        # While rule_stack is used to keep track of the rules defined in Defs
        # _out_pointer_stack keeps track of the output so that we append key-value to the correct
        # nested element in _out
        #
        # Ex:
        # For rule_stack [ISA, GS, ST] we would have
        # _out_pointer_stack ['groups', 'transactions']
        # _out = {
        #   <various interchange segment data>,
        #   'groups': [{
        #       <various groups segment data>,
        #       'transactions': [
        #           { <transaction> }, { <transaction> }
        #       ]
        #   }]
        # }
        # outPointer() --> _out['groups']['transactions']

        # When all ST(s) is completely processed and popped out of rule_stack,
        # 'transactions' will also be removed from _out_pointer_stack
        # so that we can continue append key-value to 'groups' of _out
        # as we continue processing rule GS of rule_stack

        self._out_pointer_stack = []

    def outPointer(self):
        _out_pointer = self._out
        for step in self._out_pointer_stack:
            _out_pointer = _out_pointer[step]
        return _out_pointer

    def currentRule(self):
        return self.rule_stack[len(self.rule_stack) - 1]

    def fromFile(self, path):
        logging.debug('*********** reading from file %s', path)
        with open(path, 'r') as reader:
            line = reader.readline()
            while line != '':  # The EOF char is an empty string
                lineRstrip = line.rstrip()
                logging.debug('LINE: %s', lineRstrip)
                elementArr = lineRstrip.split(Token.elementSeparator)
                isOk = self.parseElement(elementArr)
                if (isOk == False):
                    logging.debug('ERROR:\n %s', self.err)
                    break
                line = reader.readline()
        logging.debug('OUTPUT:\n %s', self._out)
        return self._out

    # Each EDI segment is converted to list previously in 'fromFile' func
    # Ex: BIG*20101204*217224*20101204*P792940
    # --> [BIG, 20101204, 217224, 20101204, P792940] (elementArr)
    def parseElement(self, element_arr):

        # Retrieve segment name as first element in list
        seg_name = element_arr[0].upper()
        logging.debug('segment name: %s', seg_name)

        # Retrieve current rule's child elements from stack
        current_rule = self.currentRule()
        subsegs = current_rule['subsegs']

        # Case 1: seg_name is defined in rule, that means end-of-rule is not encountered yet
        # Continue parsing using current_rule
        if (seg_name in subsegs):
            logging.debug('[%s] found in sub-segments', seg_name)
            seg_rule = subsegs[seg_name]

            segtype = seg_rule['segtype'] if 'segtype' in seg_rule else SegmentType.REGULAR
            logging.debug('[%s] segment type: %s', seg_name, segtype)

            # Case 1.1: Regular segment
            if (segtype in [SegmentType.REGULAR, SegmentType.LOOP]):
                logging.debug('[%s] parsing regular segment', seg_name)
                _parsed_seg = self.parse_regular_segment(seg_name, element_arr)
                _out_pointer = self.outPointer()

                # Segment of type Loop should be handled as List within the parent segment
                if (segtype == SegmentType.LOOP):
                    logging.debug(
                        '[%s] special handling for loop segment', seg_name)
                    # Create a new list with one empty element in _out and update pointers
                    # Look up loop name from Defs to wrap around the list,
                    # if loop-name not defined, automatically construct it from base segment name
                    if (seg_name in Defs.loopName):
                        loop_name = Defs.loopName[seg_name]
                        logging.debug(
                            '[%s] loop name is defined as [%s]', seg_name, loop_name)
                    else:
                        loop_name = seg_name + 's'
                        logging.debug(
                            '[%s] loop name is not defined. Auto construct: [%s]', seg_name, loop_name)

                    # TODO: Handle subsequent elements in list
                    _out_pointer[loop_name] = [{}]
                    _out_pointer = _out_pointer[loop_name][0]
                    self._out_pointer_stack.append(loop_name)
                    self._out_pointer_stack.append(0)

                _out_pointer.update(_parsed_seg)

                if ('subsegs' in seg_rule or 'subsegs_link' in seg_rule):
                    logging.debug('[%s] contains sub-segments', seg_name)
                    # This means this segment contains child elements of itself
                    # The next element(s) should be parsed using the its nested rule
                    self.rule_stack.append(seg_rule)
                    logging.debug('append rule to stack')

                    if ('subsegs_link' in seg_rule):
                        map_idx = seg_rule['subsegs_link']['mapped_by_index']
                        map_to = seg_rule['subsegs_link']['mapped_with']
                        self.rule_stack.append(map_to[element_arr[map_idx]])
                        logging.debug('append rule to stack')

            # Case 1.2:
            # End-of-rule encountered now, that means there's no more child element for this rule
            # Remove current_rule from stack so that we can continue parsing its parent rule
            elif (segtype == SegmentType.CLOSING):
                logging.debug('[%s] closing segment', seg_name)
                self.rule_stack.pop()
                logging.debug('remove rule from stack')

            # Case 1.3:
            # Segment elements in (key, value(s)) format
            # Example segments of this case are REF, DTM
            elif (segtype == SegmentType.KV_PAIR):
                logging.debug('[%s] parsing kv-pair segment', seg_name)
                _parsed_seg = self.parse_key_value_pair_segment(
                    element_arr, seg_rule['key_idx'])
                self.outPointer().update(_parsed_seg)

            return True

        # Case 2: seg_name is not defined in rule
        # Either segment definition is missing (un-implemented case), or
        # an end-of-loop signal
        else:
            # Check if we are processing sub-segments of a LOOP
            if ('segtype' in current_rule and current_rule['segtype'] == SegmentType.LOOP):
                # Rule-end -> Remove last element(s) in stack
                # and retry parsing using previous rule
                logging.debug('[%s] end-of-loop', seg_name)
                self.rule_stack.pop()
                self._out_pointer_stack.pop()  # Remove loop array index
                self._out_pointer_stack.pop()  # Remove loop name
                self.parseElement(element_arr)
            else:
                # TODO: Handle 'missing segment definition' error
                pass

            return True

    def parse_regular_segment(self, segment_name, element_arr):
        # Mapping elements of input segment and Defs.segmentDef
        # one-by-one to retrieve the names of the EDI segment element values
        counter = 1
        element_arr_len = len(element_arr)
        _seg_out = {}
        if segment_name in Defs.segmentDef:
            template = Defs.segmentDef[segment_name]
            for template_element in template:
                _seg_out[template_element] = element_arr[counter]
                counter = counter + 1
                if (element_arr_len <= counter):
                    break
            return _seg_out
        else:
            # Missing segment definition in Defs
            # Should specify an error and terminate parser
            # TODO: Handle error
            return {}

    def parse_key_value_pair_segment(self, element_arr, key_idx):
        if len(element_arr) > key_idx:
            key = element_arr[key_idx]
            # Remove segment name and the key from element_arr
            # the one(s) left are the value(s)
            # Currently assume that this segment type is always of length 3 segment_name*key*value
            element_arr.remove(key)
            element_arr.remove(element_arr[0])
            value = element_arr[0]
            return {
                key: value
            }
        else:
            # TODO: Handle error
            return {}

